# Mission environments and hazards

**Unreviewed draft: needs clinician review before live guidance.**

This page is the environment and sensor model for Crew Health Check. It says, for each mission environment, which hazards apply, what harm each can do and where in the body, what can be measured and in which unit, which NASA limits exist, and what the app tells the crew at each warning level. The same content, in machine-readable form, is in [environment-hazards.json](environment-hazards.json). The text of every source is saved in [research/environment/](research/environment/), and every quote below was checked by script against that saved text, except the quotes from N07 to N11 in section 19, which were read on 14 November 2026 for the plan aboard and are not yet saved there.

## How to read this page

* **Warning levels.** For your information (`info`), Keep an eye on it (`watch`), Act now (`act`), Emergency (`emergency`). A level comes from a sensor limit or a rule. A level can appear more than once when a hazard has several limits.
* **Habitat limits are not diagnoses.** NASA's limits for air, water, noise and radiation are limits for the habitat and the program. A reading above a limit is a reason to act on the habitat and to ask how people feel. It never proves that a symptom comes from that cause, and a normal reading never rules a symptom out.
* **Where a number is missing.** When no source gives a threshold, the value is empty and the rule says "threshold to be set by clinical review". For temperature with humidity, the NASA limit is an area on a chart, so the value is also empty and the rule gives the corner points.
* **Spacecraft Maximum Allowable Concentrations (SMACs)** are NASA's air limits for each chemical over 1 hour, 24 hours, 7, 30, 180 and 1000 days. The 1 hour and 24 hour values are for accidents only (EV03). The current table, JSC 20584 Revision D (EV03), is printed as images. This page reads the values from Revision C (EV24), whose table is text, and checked them against an OCR reading of Revision D. Revision D changed only 2-propanol, 2-butanone (MEK), acetaldehyde and 1-propanol, and moved to ppm units (EV03); none of the compounds below changed.
* **Reading a SMAC quote.** In the Revision C text, each chemical's row is printed as: 24 hour, 7 day, 30 day and 180 day values in ppm, then the five matching mg/m3 values in brackets, then the 1 hour value; the 1000 day value follows the chemical name. For example `100 55 15 15(485) (114) (63) (17) (17)425` for carbon monoxide means 425 ppm for 1 hour, 100 for 24 hours, 55 for 7 days, and 15 for 30 and 180 days. Quote fragments that contain a vertical bar come from the OCR reading of the Revision D table, which prints one row per duration with the chemicals side by side; they show the value is still present in Revision D, and the column it belongs to is the team's reading of that row.
* **Units.** NASA states SMACs in ppm, and they apply at every cabin pressure (EV03). CO2 and oxygen limits are partial pressures in mmHg: convert a ppm or % reading with the real cabin pressure. The formula and the 760 mmHg assumption for the replay data are in [NASA-SOURCES.md](NASA-SOURCES.md), section 2.
* **Revisions.** NASA-STD-3001 Volume 2 Revision F (EV01, approved 14 July 2026) is current. Older briefs sometimes quote earlier revisions; where they differ, Revision F wins. One example: the acoustics brief (EV09) describes the daily noise dose as an 8 hour 85 dBA average, while Revision F defines it as 75 dBA over 8 hours (70 dBA over 24 hours).
* **Low-cost sensors.** Each sensed hazard says what an inexpensive sensor a hackathon team might bring would measure and what it cannot tell. This is a description, not a purchase recommendation. Statements marked "team note" are the team's own engineering judgment and are not from a source read here.

## What is different here for your health

### Space station, low Earth orbit (`station_leo`)

You float, so body fluids move toward your head and your bones and muscles carry no load (EV26). Earth's magnetic field still shields you from much space radiation, but the orbit passes through a zone of trapped particles called the South Atlantic Anomaly (EV05, EV28). The air is made and cleaned by machines in a closed cabin, so CO2, chemical vapors, microbes and noise build up unless the systems keep up (N04, C25). Coolant ammonia and visiting vehicle propellants are possible leak sources (EV07, EV01).

Gravity: Microgravity (weightless). Delay to Earth: Near real time. The sources read give no delay figure for low Earth orbit; the ground team is close at hand.

Hazards that apply: [radiation](#radiation), [co2](#co2), [oxygen](#oxygen), [pressure_dcs](#pressure-dcs), [toxic_combustion](#toxic-combustion), [toxic_ammonia](#toxic-ammonia), [toxic_propellants](#toxic-propellants), [toxic_vocs](#toxic-vocs), [dust](#dust), [noise](#noise), [thermal_humidity](#thermal-humidity), [water_quality](#water-quality), [microbial](#microbial), [microgravity](#microgravity), [isolation_confinement](#isolation-confinement), [circadian_light](#circadian-light), [distance_autonomy](#distance-autonomy).

### Deep-space transit (`transit`)

You are outside Earth's protective magnetic field, so cosmic rays and solar storms reach the ship (EV28). Shielding stops most solar particles but works poorly against cosmic rays, so the ship has a storm shelter for solar events (EV05). You float for months, with the same bone, muscle, fluid shift and eye changes as on a station (EV26, S12). Messages to Earth take longer every day, so the crew must solve problems on its own (EV25, EV20).

Gravity: Microgravity (weightless). Delay to Earth: Grows with distance from Earth. Up to 20 minutes one way near Mars (EV25).

Hazards that apply: [radiation](#radiation), [co2](#co2), [oxygen](#oxygen), [pressure_dcs](#pressure-dcs), [toxic_combustion](#toxic-combustion), [toxic_ammonia](#toxic-ammonia), [toxic_propellants](#toxic-propellants), [toxic_vocs](#toxic-vocs), [dust](#dust), [noise](#noise), [thermal_humidity](#thermal-humidity), [water_quality](#water-quality), [microbial](#microbial), [microgravity](#microgravity), [isolation_confinement](#isolation-confinement), [circadian_light](#circadian-light), [distance_autonomy](#distance-autonomy).

### Lunar surface base (`lunar_surface`)

The Moon itself gives significant radiation protection compared with free space, but surface walks during a solar event are dangerous (EV05). Fine, sharp lunar dust comes inside on suits; it irritates the eyes, scratches the skin and can harm the lungs (EV08). Frequent surface walks mean frequent pressure changes and a risk of decompression sickness (EV12). The habitat may run at a lower pressure with more oxygen, for example 8.2 psia and 34% oxygen, which makes walks easier but helps fires burn (EV01, EV14).

Gravity: About one sixth of Earth's gravity (EV01, EV23). Delay to Earth: Seconds. NASA gives 6 to 10 seconds on the surface of the Moon as an example of a small delay (EV01).

At the lunar South Pole the link also comes and goes: Earth is in view for only about half of every month (N07), and the trip home takes days (EV25). See [Distance from Earth](#distance-autonomy).

Hazards that apply: [radiation](#radiation), [co2](#co2), [oxygen](#oxygen), [pressure_dcs](#pressure-dcs), [toxic_combustion](#toxic-combustion), [toxic_ammonia](#toxic-ammonia), [toxic_propellants](#toxic-propellants), [toxic_vocs](#toxic-vocs), [dust](#dust), [noise](#noise), [thermal_humidity](#thermal-humidity), [water_quality](#water-quality), [microbial](#microbial), [partial_gravity](#partial-gravity), [isolation_confinement](#isolation-confinement), [circadian_light](#circadian-light), [distance_autonomy](#distance-autonomy).

### Mars surface base (`mars_surface`)

Radiation on the surface is lower than in free space but continues every day; NASA's surface mission analysis uses about 0.5 mSv per day (EV02). Martian dust is fine, highly oxidized and salty, with heavy metals and perchlorate, and NASA sets a stricter dust limit than for the Moon (EV23, EV01). Local water and crops can carry perchlorate (EV01). After months of weightlessness in transit, the body must readjust to partial gravity before hard tasks (EV26). The ground answers late, so the crew decides alone in an emergency (EV25).

Gravity: About three eighths of Earth's gravity (N04); NASA also rounds it to about one third (EV26). Delay to Earth: Up to 20 minutes one way (EV25).

Solar conjunction cuts or degrades contact for one to several weeks about every 26 months (N10, N11), and there is no return option. See [Distance from Earth](#distance-autonomy).

Hazards that apply: [radiation](#radiation), [co2](#co2), [oxygen](#oxygen), [pressure_dcs](#pressure-dcs), [toxic_combustion](#toxic-combustion), [toxic_ammonia](#toxic-ammonia), [toxic_propellants](#toxic-propellants), [toxic_vocs](#toxic-vocs), [perchlorate](#perchlorate), [dust](#dust), [noise](#noise), [thermal_humidity](#thermal-humidity), [water_quality](#water-quality), [microbial](#microbial), [partial_gravity](#partial-gravity), [isolation_confinement](#isolation-confinement), [circadian_light](#circadian-light), [distance_autonomy](#distance-autonomy).

### Spacewalk or surface walk (activity overlay) (`eva`)

Outside the habitat the suit is your only shelter. Pressure drops from cabin pressure to the lower suit pressure, so nitrogen can form bubbles in the body unless you prebreathe oxygen first (EV12). CO2 can build up inside the helmet (EV15). Shielding is thin; real-time radiation alerts with the walk ended within 2 hours give a very high level of protection (EV05). On the Moon and Mars, dust comes back in on the suit (EV08). After the walk, ammonia and propellants need checking before the suit comes into the cabin (EV01).

Gravity: Same as the host environment. Delay to Earth: Same as the host environment.

Hazards that apply: [radiation](#radiation), [co2](#co2), [oxygen](#oxygen), [pressure_dcs](#pressure-dcs), [toxic_ammonia](#toxic-ammonia), [toxic_propellants](#toxic-propellants), [perchlorate](#perchlorate), [dust](#dust), [thermal_humidity](#thermal-humidity), [microgravity](#microgravity), [partial_gravity](#partial-gravity), [distance_autonomy](#distance-autonomy).

## Hazards at a glance

| Hazard | Kind | Space station | Deep-space transit | Lunar surface base | Mars surface base | Spacewalk or surface walk |
|---|---|---|---|---|---|---|
| [Ionizing radiation](#radiation) | sensed | yes | yes | yes | yes | yes |
| [Carbon dioxide (CO2)](#co2) | sensed | yes | yes | yes | yes | yes |
| [Oxygen (too little or too much)](#oxygen) | sensed | yes | yes | yes | yes | yes |
| [Total pressure and decompression sickness](#pressure-dcs) | sensed | yes | yes | yes | yes | yes |
| [Fire and combustion products (carbon monoxide, hydrogen cyanide, acid gases)](#toxic-combustion) | sensed | yes | yes | yes | yes |  |
| [Ammonia (coolant leak)](#toxic-ammonia) | sensed | yes | yes | yes | yes | yes |
| [Propellants (hydrazine family, nitrogen tetroxide)](#toxic-propellants) | sensed | yes | yes | yes | yes | yes |
| [Chemical vapors (formaldehyde and other volatile organic compounds)](#toxic-vocs) | sensed | yes | yes | yes | yes |  |
| [Martian perchlorates](#perchlorate) | sensed |  |  |  | yes | yes |
| [Dust and particles (cabin, lunar and Martian dust)](#dust) | sensed | yes | yes | yes | yes | yes |
| [Noise](#noise) | sensed | yes | yes | yes | yes |  |
| [Temperature and humidity](#thermal-humidity) | sensed | yes | yes | yes | yes | yes |
| [Drinking water quality](#water-quality) | sensed | yes | yes | yes | yes |  |
| [Microbes in air and on surfaces](#microbial) | sensed | yes | yes | yes | yes |  |
| [Weightlessness (microgravity)](#microgravity) | condition | yes | yes |  |  | yes |
| [Partial gravity (Moon and Mars)](#partial-gravity) | condition |  |  | yes | yes | yes |
| [Isolation and confinement](#isolation-confinement) | condition | yes | yes | yes | yes |  |
| [Body clock and light](#circadian-light) | condition | yes | yes | yes | yes |  |
| [Distance from Earth (communication delay and autonomy)](#distance-autonomy) | condition | yes | yes | yes | yes | yes |

<a id="radiation"></a>

## 1. Ionizing radiation

**In plain words.** Invisible, fast particles from the Sun and from deep space pass through the body and can damage cells.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Higher lifetime risk of cancer | not one place | EV05, EV02 |
| Nausea, vomiting and tiredness after a large solar particle event (acute radiation syndrome) | abdomen | EV05 |
| Skin injury and loss of blood-forming cells after large doses | not one place | EV05 |
| Cataract (clouding of the lens of the eye) | eyes | EV02 |
| Changes in thinking, movement and behavior | head | EV05 |
| Harm to the heart and blood vessels (NASA sets circulatory dose limits) | heart | EV02 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Inside Earth's magnetic field. Short passes through the South Atlantic Anomaly add trapped-particle dose; normal shielding is enough for these (EV05).
* **Deep-space transit:** Outside Earth's magnetic field (EV28). Unshielded cosmic-ray dose in free space is about 1.5 mSv per day at solar minimum; ship designs for stays over 60 days aim below 1.3 mSv per day (EV02). Solar particle events need a storm shelter (EV05).
* **Lunar surface base:** The lunar surface gives significant protection compared with free space (EV05). Designs for stays over 60 days aim below 0.9 mSv per day on planetary surfaces (EV02). Surface walks during a solar event are the main danger (EV05).
* **Mars surface base:** About 0.5 mSv per day from the space environment in NASA's surface mission analysis (EV02). Extra shielding on Mars helps little unless it is rich in hydrogen (EV02).
* **Spacewalk or surface walk (activity overlay):** Thin shielding. Real-time alert dosimetry gives a very high level of protection if the walk ends within 2 hours and shelter is available (EV05).

**What is measured.** Dose rate in the habitat (area monitor) and the dose each person receives (personal dosimeter). NASA requires both, plus alerts when dose rates exceed thresholds the program sets in advance (EV01). Main unit: mSv (effective dose, used for NASA limits). Other units: µGy per hour (absorbed dose rate from instruments, as in the package's DosTel replay sample); mGy-Eq (organ dose limits); counts per minute (Geiger counters).

**A low-cost sensor a hackathon team could bring.** A handheld Geiger counter counts ionizing events and usually shows an estimated dose rate. **What it cannot tell:** A Geiger counter counts events. It cannot tell particle type or energy. NASA dose monitoring needs radiation type, linear energy transfer, intensity and direction (EV01), and the biological effect per unit dose differs by particle type (EV02, Table 4.8-2). So it cannot give a NASA effective dose or any one person's dose. Team note, not from a source read here: handheld counters are usually calibrated with gamma rays on Earth, not space particles.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Show today's dose rate and the running mission dose next to the career limit: total career effective dose stays below 600 mSv, for all ages and sexes, counting every past mission. | 600 mSv | career (all missions) | EV02 |
| Keep an eye on it | Dose rate rises above the habitat's usual background, for example as a solar event builds. NASA requires the program to predefine the alert threshold; until then, threshold to be set by clinical review. | none | continuous | EV01 |
| Act now | Radiation alert or solar particle event warning: shelter now and end any walk outside. NASA requires the program to predefine the alert threshold; until then, threshold to be set by clinical review. | none | continuous | EV01 |
| Act now | Projected 30-day dose to the blood-forming organs would pass 250 mGy-Eq (the 30-day limit). How close counts as a warning is a threshold to be set by clinical review. | 250 mGy-Eq | 30 days | EV02 |
| Emergency | Anyone has nausea, vomiting or unusual tiredness during or after a radiation event (possible acute radiation syndrome). Dose threshold to be set by clinical review. | none | during or after an event | EV05 |

Exact quotes behind these levels:

* For your information (EV02): "total career effective radiation dose due to spaceflight radiation exposure shall be less than 600 mSv"
* Keep an eye on it (EV01): "alert the crew and operations teams when radiation exposure rates exceed predefined thresholds"
* Act now (EV01): "The crew must be able to follow sheltering procedures planned in the event that dose rates exceed predefined thresholds."
* Act now (EV02): "Blood-forming Organs# 250 mGy-Eq 500 mGy-Eq Not applicable"
* Emergency (EV05): "Acute radiation syndromes, such as nausea, vomiting, and fatigue, may occur due to in mission radiation exposure"

**What the app tells the crew**

* **For your information:** Nothing to do. Wear your personal dosimeter. NASA keeps every exposure as low as reasonably achievable, even far below the limits (EV02).
* **Keep an eye on it:** Know where the storm shelter is. Wrap up work in thinly shielded areas. If you are outside, stay close to the habitat.
* **Act now:** Go to the storm shelter now. If you are outside, end the walk and come back in. Stay in the shelter until the all clear.
* **Emergency:** Press Someone needs help. Stay in the shelter. Note when the symptoms started. Log it for the ground.

**Routine check.** continuous: dose rate on screen (NASA says the radiation sampling rate depends on system design and mission, EV01 Table 6.1-1). per_eva: read the personal dosimeter after each walk. after_event (solar particle event): record shelter time and dose. How often to read personal dosimeters: frequency to be set by the flight surgeon.

**Sources:** EV01, EV02, EV05, EV28.

<a id="co2"></a>

## 2. Carbon dioxide (CO2)

**In plain words.** CO2 is the gas you breathe out. In a closed cabin it builds up unless machines remove it.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Headache | head | C25, EV07 |
| Shortness of breath | lungs | C25 |
| Tiredness | not one place | C25 |
| Vision changes | eyes | EV07 |
| Nausea | abdomen | EV07 |
| Unconsciousness in severe overexposure | head | EV07 |
| Poorer sleep | not one place | C15 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Removed by scrubbers. NASA's brief gives an ISS range of 1 to 9 mmHg (C25). Pockets can form around the face during sleep if the air does not move (C15).
* **Deep-space transit:** Same machines and the same limit. When the average stays high for days, degraded scrubbers must be found and repaired or extra scrubbing switched on (C25); far from Earth the crew does more of this work itself (EV25).
* **Lunar surface base:** Same 3 mmHg limit. In a low-pressure habitat the same ppm reading is a lower partial pressure, so always convert with the real cabin pressure (EV03 makes the same point for other gases).
* **Mars surface base:** Same limit and the same conversion care as on the Moon.
* **Spacewalk or surface walk (activity overlay):** CO2 can build up inside the helmet when suit gases are not fully washed out (EV15). Suit limits are set separately from cabin limits (EV15).

**What is measured.** CO2 in the cabin air, averaged over one hour (the NASA limit) and over seven days (headache risk context). Main unit: mmHg (partial pressure, ppCO2). Other units: ppm by volume (ppCO2 in mmHg = ppm x cabin pressure in mmHg / 1,000,000, see NASA-SOURCES.md section 2); kPa.

**A low-cost sensor a hackathon team could bring.** An NDIR (non-dispersive infrared) CO2 module reads ppm. **What it cannot tell:** It reads ppm at one spot. To compare with the mmHg limit you need the actual cabin pressure. One fixed sensor misses local pockets that are not measured by fixed sensors (C25). It cannot tell whether a headache comes from CO2: fluid shift in weightlessness causes similar headaches (C25).

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | One-hour average at or below 3 mmHg: within the NASA limit. | 3 mmHg | 1-hour average | EV01 |
| Keep an eye on it | Seven-day average at or above 2.5 mmHg. NASA's CO2 brief says staying below 2.5 mmHg keeps headache risk below 1%. Context value, not a NASA-STD-3001 requirement. | 2.5 mmHg | 7-day average | C25 |
| Act now | One-hour average above 3 mmHg (NASA-STD-3001 Volume 2 Rev F, V2 6004). An environmental limit for the cabin, not a diagnosis. | 3 mmHg | 1-hour average | EV01 |
| Emergency | Off-nominal CO2 values are left to each program, so the emergency level is a threshold to be set by clinical review. Raise Emergency at once if anyone is confused, very short of breath or hard to wake. | none | any moment | EV01 |

Exact quotes behind these levels:

* For your information (EV01): "limit the average one-hour CO2 partial pressure (ppCO2) in the habitable volume to no more than 3 mmHg"
* Keep an eye on it (C25): "To keep the risk of headache below 1%, average 7 [...] needs to be maintained below 2.5 mmHg"
* Emergency (EV01): "Off-nominal CO2 values are not included within this NASA Technical Standard"

**What the app tells the crew**

* **For your information:** Nothing to do. Keep air moving around your face when you sleep (C15).
* **Keep an eye on it:** Record any headache with its time and place in the daily check. Make sure vents and fans near you are not blocked.
* **Act now:** Tell the crew and the ground. Move to a well-ventilated module. Report headache, breathlessness or tiredness. The flight control team finds and repairs the CO2 scrubbers or switches on extra scrubbing (C25).
* **Emergency:** Press Someone needs help. Move the person to another module with fresh air. Follow the vehicle's breathing protection procedure. Log it for the ground.

**Routine check.** continuous: NASA's example sampling rate for atmosphere constituents is every 5 seconds (EV01 Table 6.1-1). daily: headache question in the daily check. after_event: a reported headache opens the CO2 trend for the place the person was.

**Sources:** EV01, C25, EV07, EV15, C15, EV03.

<a id="oxygen"></a>

## 3. Oxygen (too little or too much)

**In plain words.** Too little oxygen dulls your thinking and vision, often without you noticing. Too much oxygen helps fires burn and can injure the lungs.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Mental changes, at times not noticed by the person | head | EV06 |
| Worse color, night and side vision | eyes | EV06 |
| Tiredness, nausea, headache, dizziness and mood changes | head | EV06 |
| Fast breathing (hyperventilation) | lungs | EV06 |
| Poorer sleep | not one place | EV06 |
| Small areas of lung collapse (absorption atelectasis) when oxygen is above half the air | lungs | EV01 |
| Oxygen toxicity: breathing problems or tissue injury | lungs | EV06 |
| Fire: materials burn more easily and hotter in oxygen-rich air | not one place | EV14, EV01 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Air like sea level. The normal target range applies.
* **Deep-space transit:** Same as a station; all oxygen comes from on-board systems.
* **Lunar surface base:** The habitat may run at lower pressure with more oxygen, for example 8.2 psia and 34% oxygen, to make walks easier (EV01). Mild low oxygen is allowed only with a health monitoring plan (EV01). A higher oxygen fraction raises fire risk (EV14).
* **Mars surface base:** Same as the lunar base.
* **Spacewalk or surface walk (activity overlay):** The suit uses pure oxygen at low pressure; NASA's current suit runs at 4.3 psia with 100% oxygen (EV01). Oxygen prebreathe comes before the walk (EV12).

**What is measured.** Oxygen partial pressure in the cabin, used to work out the inspired oxygen pressure (PIO2) that NASA's limits use. Main unit: mmHg (PIO2, inspired oxygen partial pressure). Other units: % oxygen by volume; kPa; psia. PIO2 is the barometric pressure minus water vapor in the lungs, times the oxygen fraction (EV01 Table 6.2-1; the formula did not extract cleanly as text, so read it from the PDF)..

**A low-cost sensor a hackathon team could bring.** An electrochemical oxygen cell reads % oxygen (some read partial pressure). A barometric pressure sensor gives total pressure; the two together give ppO2. **What it cannot tell:** A % reading alone does not tell how much oxygen your lungs get; you also need total pressure. Gas sensors can lose sensitivity over time (EV32). It cannot measure a person's blood oxygen; that needs the pulse oximeter.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | PIO2 in the normal target range of 145 to 155 mmHg. | 145 mmHg | continuous | EV01 |
| Keep an eye on it | PIO2 below 145 mmHg. Mild low oxygen down to 127 mmHg is allowed only when planned, with health monitoring. | 145 mmHg | continuous | EV01 |
| Keep an eye on it | Oxygen fraction above the mission's planned set point (fire risk). For context, at sea-level pressure OSHA calls air above 23.5% oxygen enriched; planned exploration atmospheres run higher on purpose. | 23.5 % oxygen by volume | continuous | EV01 |
| Act now | PIO2 one-hour average below 127 mmHg (the mild hypoxia lower limit). | 127 mmHg | 1-hour time-weighted average | EV01 |
| Act now | PIO2 above 356 mmHg outside a planned prebreathe or treatment (above the indefinite hyperoxia limit). | 356 mmHg | continuous | EV01 |
| Emergency | PIO2 below 122 mmHg at any moment, or anyone shows signs of low oxygen such as confusion or worse vision. | 122 mmHg | any moment (absolute lower limit) | EV01, EV06 |

Exact quotes behind these levels:

* For your information (EV01): "(mmHg) 145-155 356 791 127"
* Keep an eye on it (EV01): "Indefinite with monitoring"
* Keep an eye on it (EV01): "oxygen-enriched atmosphere as >23.5% oxygen by volume"
* Act now (EV01): "1-hour time-weighted average with an absolute lower limit for the minimum hypoxia range of 122 mmHg/2.36 psia"
* Emergency (EV01, EV06): "1-hour time-weighted average with an absolute lower limit for the minimum hypoxia range of 122 mmHg/2.36 psia [...] Crew are trained to don oxygen masks anytime symptoms of hypoxia are experienced."

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Check how you and your crewmates feel; low oxygen can change thinking without you noticing (EV06). For high oxygen, keep ignition sources away and follow the fire prevention rules.
* **Act now:** Put on an oxygen mask if you have any symptom (EV06). Tell the crew and the ground.
* **Emergency:** Put on oxygen masks now and help crewmates with theirs (EV06). Press Someone needs help. Watch each other: symptoms are easier to see in others (EV06).

**Routine check.** continuous: NASA's example sampling rate for atmosphere constituents is every 5 seconds (EV01 Table 6.1-1). per_eva: prebreathe log. mission_phase: any change of cabin atmosphere set point.

**Sources:** EV01, EV06, EV14, EV21, EV12, EV32.

<a id="pressure-dcs"></a>

## 4. Total pressure and decompression sickness

**In plain words.** Air pressure keeps nitrogen dissolved in your body. A fast drop, such as moving into a suit, can make it form bubbles (decompression sickness, also called the bends).

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Joint and muscle pain | not one place | EV12 |
| Itching, mild rashes or a mottled skin rash | not one place | EV12 |
| Numbness and weakness | not one place | EV12 |
| Problems thinking | head | EV12 |
| Inner ear problems | ear | EV12 |
| Heart and lung problems | lungs | EV12 |
| Ear and lung injury from fast pressure changes (barotrauma) | ear | EV01 |
| Low oxygen if the cabin loses pressure | lungs | EV21 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Sea-level cabin pressure. Decompression sickness risk comes with spacewalks at low suit pressure (EV12).
* **Deep-space transit:** Few walks expected; the main pressure concern is a cabin leak.
* **Lunar surface base:** Frequent walks. A lower cabin pressure, such as 8.2 psia, reduces the pressure drop into the suit (EV01, EV12).
* **Mars surface base:** As on the Moon. Treatment has to happen on site because timely return to Earth is not feasible (EV01).
* **Spacewalk or surface walk (activity overlay):** The main risk. Prebreathe pure oxygen before the walk (EV12) and report any symptom at once.

**What is measured.** Total cabin pressure and how fast it changes. Main unit: kPa. Other units: psia; mmHg; hPa (most barometric sensors).

**A low-cost sensor a hackathon team could bring.** A digital barometric pressure sensor (the kind in phones and weather stations) reads absolute pressure. **What it cannot tell:** It cannot see bubbles in the body or tell who has decompression sickness. NASA diagnoses it from symptoms; bubbles can be present without symptoms, and symptoms can occur without detectable bubbles (EV12).

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Cabin pressure inside the range for an indefinite stay: above 34.5 kPa (5 psia) and up to 103 kPa (15.0 psia). | 34.5 kPa | continuous | EV01 |
| Keep an eye on it | Unplanned slow drop from the pressure set point. Threshold to be set by clinical review. | none | continuous | none |
| Act now | Pressure changing faster than 13.5 psi per minute (for changes larger than 1 psi), or falling to 34.5 kPa (5 psia) or below. | 13.5 psi per minute | any change larger than 1 psi | EV01 |
| Emergency | Fast pressure loss, or any decompression sickness symptom after a walk or pressure change: joint pain, itching or rash, numbness, weakness, confusion, ear or breathing problems. | none | after any pressure drop | EV12 |

Exact quotes behind these levels:

* For your information (EV01): "kPa < pressure ≤ 103 kPa (5 psia < pressure ≤ 15.0 psia) for indefinite human exposure"
* Act now (EV01): "the rate of change of total internal vehicle pressure shall not exceed 13.5 psi/min"
* Emergency (EV12): "Musculoskeletal pain [...] Neurological symptoms [...] Numbness [...] Weakness [...] Cognitive issues [...] Inner ear problems [...] Cardiopulmonary issues"

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Tell the crew. Know the way to the safe module and where the masks are.
* **Act now:** Follow the leak procedure. During a planned pressure change, ask for a pause if your ears hurt; NASA requires the system to pause within 1 psi of the request (EV01).
* **Emergency:** Press Someone needs help. For decompression sickness, quick treatment works better: NASA aims to return the person to their starting pressure as soon as possible (EV01, EV12). Log it for the ground.

**Routine check.** continuous: NASA's example sampling rate for pressure is every 1 second (EV01 Table 6.1-1). per_eva: confirm prebreathe was completed, and run a decompression symptom check after every walk; how long after the walk: frequency to be set by the flight surgeon.

**Sources:** EV01, EV12, EV21.

<a id="toxic-combustion"></a>

## 5. Fire and combustion products (carbon monoxide, hydrogen cyanide, acid gases)

**In plain words.** A fire or an overheating part releases poisonous gases. Carbon monoxide has no smell and no color.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Headache, dizziness and weakness from carbon monoxide | head | EV07 |
| Upset stomach and vomiting | abdomen | EV07 |
| Chest pain | heart | EV07 |
| Confusion | head | EV07 |
| Irritant gases from burning materials; toxic combustion products contributed to the deaths in the Apollo 1 fire | lungs | EV07 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** In spaceflight, carbon monoxide most likely comes from a fire (EV07).
* **Deep-space transit:** No nearby escape. Masks, stopping ventilation and closing hatches are the response (EV14).
* **Lunar surface base:** A higher oxygen fraction makes materials ignite more easily and burn hotter (EV14).
* **Mars surface base:** As on the Moon.
* **Spacewalk or surface walk (activity overlay):** Not applicable during the walk.

**What is measured.** Carbon monoxide, hydrogen cyanide and acid gases (HCl, HF) in the cabin air. Main unit: ppm. Other units: mg/m3 (depends on cabin pressure, EV03).

**A low-cost sensor a hackathon team could bring.** An electrochemical carbon monoxide sensor reads ppm. A particle sensor rises with smoke. **What it cannot tell:** A low-cost electrochemical cell can respond to more than one gas (EV33 describes a cell that responds to both NO2 and O3). It does not measure hydrogen cyanide or acid gases. NASA recommends a carbon monoxide monitor covering 5 to 1000 ppm with 10% accuracy and 1 ppm resolution (EV01 Table 6.2-3); compare any sensor with that.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Carbon monoxide at or below 15 ppm, the long-term SMAC (30, 180 and 1000 days). | 15 ppm | 30 to 1000 days | EV24 |
| Keep an eye on it | Carbon monoxide above 15 ppm (30 to 1000 day SMAC). Above 55 ppm also passes the 7-day SMAC. | 15 ppm | 30 to 1000 days | EV24 |
| Act now | Carbon monoxide above 100 ppm (24-hour SMAC). Short-term SMACs are for off-nominal events. | 100 ppm | 24 hours | EV24, EV03 |
| Emergency | Carbon monoxide above 425 ppm (1-hour SMAC). | 425 ppm | 1 hour | EV24, EV03 |
| Emergency | Smoke, flame, burning smell or a fire alarm. Do not wait for a gas reading before putting on a mask. | none | any moment | EV14 |

Exact quotes behind these levels:

* For your information (EV24): "100 55 15 15(485) (114) (63) (17) (17)425 [...] Carbon monoxide [...] 630-08-0"
* Keep an eye on it (EV24): "100 55 15 15(485) (114) (63) (17) (17)425"
* Act now (EV24, EV03): "100 55 15 15(485) (114) (63) (17) (17)425 [...] Short-term (1- and 24-hour) SMACs apply to off-nominal situations"
* Emergency (EV24, EV03): "100 55 15 15(485) (114) (63) (17) (17)425 [...] 1 hour \| ppm \| 50 \| 50 \| 425"
* Emergency (EV14): "Contaminant Level readings are not required prior to mask donning"

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Look and smell for hot or overheating equipment. Tell the crew. The ground reviews the trend.
* **Act now:** Tell the crew and the ground. Find the source. Get masks ready. Report headache, dizziness or nausea.
* **Emergency:** Put on your mask now (EV14). Follow the fire procedure: stop ventilation, remove power, use the extinguisher, then leave and close the hatch if the air stays bad (EV14). Press Someone needs help for anyone with symptoms.

**Routine check.** continuous: NASA's example sampling rate for combustion products is every 1 second (EV01 Table 6.1-1). after_event (fire or overheating): keep monitoring after the event until levels are safe (EV01, V2 6025).

**Sources:** EV01, EV03, EV24, EV07, EV14, EV33.

<a id="toxic-ammonia"></a>

## 6. Ammonia (coolant leak)

**In plain words.** Ammonia is a sharp-smelling gas used to cool some spacecraft. A coolant leak can release a lot of it quickly.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Coughing | lungs | EV07 |
| Nose and throat irritation | neck | EV07 |
| At high levels, burning of the nose, throat and airways that can lead to breathing failure | lungs | EV07 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Used for active cooling on the US segment of the ISS and on Orion. It can enter through a heat exchanger leak or be brought in by a fan (EV07). It also builds up slowly from human metabolism (EV07).
* **Deep-space transit:** Depends on the ship's cooling design; Orion uses ammonia (EV07).
* **Lunar surface base:** Depends on the habitat's cooling design. Some polar ice samples may hold ammonia; they must stay contained (EV08).
* **Mars surface base:** Depends on the habitat's cooling design.
* **Spacewalk or surface walk (activity overlay):** In 1998 an ammonia leak froze on a spacesuit during a walk (EV07). NASA says ammonia needs monitoring after a walk (EV01).

**What is measured.** Ammonia in the cabin or airlock air. Main unit: ppm. Other units: mg/m3 (depends on cabin pressure, EV03).

**A low-cost sensor a hackathon team could bring.** Electrochemical or metal oxide ammonia sensors exist. **What it cannot tell:** Low-cost gas sensors may not be selective for the target gas and become less sensitive over time (EV32). A cheap sensor is no substitute for the vehicle's monitor.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Ammonia at or below 3 ppm, the SMAC for 7 to 1000 days. | 3 ppm | 7 to 1000 days | EV24 |
| Keep an eye on it | Ammonia above 3 ppm, or any ammonia smell. | 3 ppm | 7 to 1000 days | EV24 |
| Act now | Ammonia above 20 ppm (24-hour SMAC). | 20 ppm | 24 hours | EV24, EV03 |
| Emergency | Ammonia above 30 ppm (1-hour SMAC), a coolant leak alarm, or a strong smell with burning eyes or throat. | 30 ppm | 1 hour | EV24, EV03 |

Exact quotes behind these levels:

* For your information (EV24): "20 3 3 3(20) (14) (2) (2) (2)30 [...] Ammonia [...] 7664-41-7"
* Keep an eye on it (EV24): "20 3 3 3(20) (14) (2) (2) (2)30"
* Act now (EV24, EV03): "20 3 3 3(20) (14) (2) (2) (2)30 [...] 24 hour \| ppm \| 50 \| 200 \| 0.035 \| 45 \| 80 \| 20"
* Emergency (EV24, EV03): "20 3 3 3(20) (14) (2) (2) (2)30 [...] 1 hour \| ppm \| 134 \| 500 \| 0.075 \| 45 \| 150 \| 30"

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Report any ammonia smell. The ground checks the trend.
* **Act now:** Get the ammonia masks ready. Tell the crew and the ground. Look for the source.
* **Emergency:** Put on the ammonia mask (NASA uses masks fitted with ammonia cartridges, EV07). Leave the area and close the hatch as the procedure says. Press Someone needs help for anyone coughing or short of breath.

**Routine check.** continuous where a monitor exists. per_eva: post-walk ammonia check before the suit enters the cabin (EV01, V2 6025). after_event: coolant leak alarm or hard landing.

**Sources:** EV07, EV24, EV03, EV01, EV08, EV32.

<a id="toxic-propellants"></a>

## 7. Propellants (hydrazine family, nitrogen tetroxide)

**In plain words.** Rocket fuels such as hydrazine and their oxidizers are very toxic. They can come inside on a suit after a walk near thrusters or a lander, or leak during descent.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Liver damage (the basis of the hydrazine SMAC) | abdomen | EV24 |
| Cancer, and tissue changes in the nose (listed effects in the hydrazine SMAC) | not one place | EV24 |
| Breathing problems and fluid in the lungs after nitrogen tetroxide exposure (Apollo-Soyuz, 1975) | lungs | EV07 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Thrusters and visiting vehicles use propellants. Check after walks near them (EV01).
* **Deep-space transit:** The ship's own thrusters; leaks are possible.
* **Lunar surface base:** Landers use propellants. Check suits and the airlock after walks near a lander.
* **Mars surface base:** As on the Moon.
* **Spacewalk or surface walk (activity overlay):** The main route: a contaminated suit brought inside. NASA says hydrazine, MMH, UDMH, nitrogen tetroxide and ammonia need monitoring after a walk (EV01).

**What is measured.** Hydrazine, monomethylhydrazine (MMH), unsymmetrical dimethylhydrazine (UDMH) and nitrogen tetroxide or nitrogen dioxide in the airlock or cabin. Main unit: ppm. Other units: ppb; mg/m3 (depends on cabin pressure, EV03).

**A low-cost sensor a hackathon team could bring.** None known to the team for hydrazine at these levels (team note). Electrochemical NO2 cells exist but can respond to other gases too (EV33). **What it cannot tell:** Low-cost sensors are not selective or sensitive enough at these very low limits (the hydrazine 180-day SMAC is 0.004 ppm).

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Hydrazine at or below 0.004 ppm (180-day SMAC). Use the SMAC that matches the mission length: 0.04 ppm for 7 days, 0.02 ppm for 30 days. | 0.004 ppm | 180 days | EV24 |
| Keep an eye on it | Hydrazine above the long-term SMAC for the mission length, or an unusual smell after a walk. | 0.004 ppm | 180 days | EV24 |
| Act now | Hydrazine above 0.3 ppm (24-hour SMAC). | 0.3 ppm | 24 hours | EV24 |
| Emergency | Hydrazine above 4 ppm (1-hour SMAC), or visible residue on a suit after a walk near thrusters. | 4 ppm | 1 hour | EV24 |

Exact quotes behind these levels:

* For your information (EV24): "0.3 0.04 0.02 0.004(5) (0.4) (0.05) (0.03) (0.005)4 [...] Hydrazine [...] 302-01-2"
* Keep an eye on it (EV24): "0.3 0.04 0.02 0.004(5) (0.4) (0.05) (0.03) (0.005)4"

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Report any unusual smell after a walk. Keep the airlock closed until it is checked.
* **Act now:** Stay out of the airlock. Put on masks. Log it for the ground.
* **Emergency:** Mask on. Leave and close off the area. Press Someone needs help for anyone with eye, throat or breathing symptoms. Log it for the ground.

**Routine check.** per_eva: check the suit and airlock for propellant before entering the cabin when the walk was near thrusters or a lander (EV01, V2 6025). after_event: landing, docking or propellant leak.

**Sources:** EV01, EV24, EV03, EV07, EV33.

<a id="toxic-vocs"></a>

## 8. Chemical vapors (formaldehyde and other volatile organic compounds)

**In plain words.** Plastics, glues, cleaning products, experiments and people slowly release chemical vapors. In a sealed cabin they add up.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Eye, nose and throat irritation | eyes | EV31, EV07 |
| Headaches, loss of coordination and nausea | head | EV31 |
| Liver damage | abdomen | EV31 |
| Kidney damage | flank | EV31 |
| Damage to the central nervous system | head | EV31 |
| Formaldehyde irritates the airways and can cause cancer after long exposure | lungs | EV07 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Off-gassing from hardware and payload chemicals. Formaldehyde has been tracked on the ISS for years (EV07).
* **Deep-space transit:** No fresh air and the same sources. NASA requires VOC monitoring with alerts (EV01, V2 6023).
* **Lunar surface base:** As in transit. SMACs in ppm apply at every habitat pressure (EV03).
* **Mars surface base:** As on the Moon.
* **Spacewalk or surface walk (activity overlay):** Not applicable during the walk.

**What is measured.** Individual compounds (for example formaldehyde) against their SMACs, and the toxicity index of each group of similar compounds. Main unit: ppm. Other units: ppb; mg/m3 (depends on cabin pressure, EV03); VOC index (unitless, from low-cost sensors).

**A low-cost sensor a hackathon team could bring.** A metal oxide VOC index sensor gives a relative index of total VOCs. **What it cannot tell:** It cannot name the chemical or give its concentration, so it cannot be compared with any SMAC. A metal oxide sensor turns a resistance change into an estimate (EV33) and may not be selective (EV32). Use it only as a 'something changed' signal.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Toxicity index below 1 for every group of similar compounds (the sum of each compound's concentration divided by its SMAC). | 1 toxicity index (unitless) | SMAC duration matching the mission | EV03 |
| Keep an eye on it | Toxicity index reaches 1 for any group, or formaldehyde above 0.1 ppm (SMAC for 7 to 1000 days). Formaldehyde SMACs are ceiling values, not averages. | 0.1 ppm | ceiling value (7 to 1000 days) | EV24 |
| Keep an eye on it | A low-cost VOC index jumps well above its usual baseline. Threshold to be set by clinical review. | none | continuous | none |
| Act now | Formaldehyde above 0.5 ppm (24-hour SMAC, a ceiling value). | 0.5 ppm | ceiling value (24 hours) | EV24 |
| Emergency | Formaldehyde above 0.8 ppm (1-hour SMAC, a ceiling value), or a chemical spill with eye or breathing symptoms. | 0.8 ppm | ceiling value (1 hour) | EV24 |

Exact quotes behind these levels:

* For your information (EV03): "the toxicity index (Tgrp) for each toxicological group of compounds is less than 1"
* Keep an eye on it (EV24): "0.5 0.1 0.1 0.1(1.0) (0.6) (0.12) (0.12) (0.12)0.8 [...] Formaldehyde [...] 50-00-0"
* Act now (EV24): "0.5 0.1 0.1 0.1(1.0) (0.6) (0.12) (0.12) (0.12)0.8"
* Emergency (EV24): "0.5 0.1 0.1 0.1(1.0) (0.6) (0.12) (0.12) (0.12)0.8 [...] REMARKS:  Ceiling values, Carcinogen"

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Note any smell, eye or throat irritation, or headache in the daily check. The ground reviews the air samples.
* **Act now:** Find and contain the source if you know it (a spill, an experiment, hot equipment). Report symptoms. Log it for the ground.
* **Emergency:** Leave the area and close the hatch. Mask on. Press Someone needs help for anyone with symptoms.

**Routine check.** every_n_days (n = 3): NASA's example sampling rate for VOCs is at least every 73 hours (EV01 Table 6.1-1). daily: irritation and headache questions in the daily check.

**Sources:** EV03, EV24, EV01, EV07, EV31, EV32, EV33.

<a id="perchlorate"></a>

## 9. Martian perchlorates

**In plain words.** Martian dust and soil contain perchlorate salts. Breathed in or swallowed, perchlorate can stop the thyroid gland in your neck from taking up iodine.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| The thyroid gland takes up less iodine; long exposure to higher amounts may lower thyroid activity (hypothyroidism) | neck | EV30 |
| Low thyroid hormone can affect the skin, heart, lungs, kidneys, gut, nerves and other organs | not one place | EV30 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Not a hazard here in the sources read.
* **Deep-space transit:** Not a hazard here in the sources read.
* **Lunar surface base:** Not a hazard here in the sources read.
* **Mars surface base:** Martian dust holds 0.5 to 1% perchlorate, 3 to 4 orders of magnitude more than soils on Earth (EV23). Water from Martian sources and crops grown in it can carry perchlorate (EV01).
* **Spacewalk or surface walk (activity overlay):** On Mars, dust on suits brings perchlorate inside.

**What is measured.** Perchlorate in water or soil samples (laboratory analysis). In air, NASA protects against it through the Martian dust limit (EV01). Main unit: mg/m3 (as Martian dust in air). Other units: mg/L (perchlorate in water, laboratory test).

**A low-cost sensor a hackathon team could bring.** None known to the team for perchlorate itself. A particle sensor can track dust (see the dust hazard). **What it cannot tell:** A dust reading does not tell how much perchlorate the dust holds. The saved water guidelines (JSC 63414 Rev A) have no perchlorate entry.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Martian dust below 0.1 mg/m3 (30-day time-weighted average). NASA says meeting this dust limit also protects from perchlorate and manganese in the dust. | 0.1 mg/m3 | 30-day time-weighted average | EV01 |
| Keep an eye on it | Water or food from a local Martian source has not been tested for perchlorate. Threshold to be set by clinical review. | none | before first use | EV01 |
| Act now | Perchlorate found in drinking water or food. No spacecraft limit for perchlorate was found in the sources read; threshold to be set by clinical review. | none | any detection | EV30 |

Exact quotes behind these levels:

* For your information (EV01): "implementation of this Martian dust limit will also protect from these compounds"
* Keep an eye on it (EV01): "perchlorate dissolution from Mars regolith into water and subsequent uptake by food crops"
* Act now (EV30): "Perchlorate's inhibition of iodine uptake must be great enough to affect the thyroid before it is considered harmful."

**What the app tells the crew**

* **For your information:** Nothing to do. Keep dust out of living areas.
* **Keep an eye on it:** Do not drink or cook with local water until it is tested.
* **Act now:** Switch to stored water. The medical officer decides on thyroid checks, as in the onboard procedure. Log it for the ground.
* **Emergency:** No emergency level: perchlorate harm builds slowly over long exposure (EV30). Use Someone needs help for any sudden illness.

**Routine check.** after_event: test every new local water source before use. per_eva: dust control after every Mars surface walk. Thyroid checks: frequency to be set by the flight surgeon.

**Sources:** EV01, EV23, EV30, EV04.

<a id="dust"></a>

## 10. Dust and particles (cabin, lunar and Martian dust)

**In plain words.** Tiny particles in the air. On the Moon and Mars the dust is very fine and sharp, and it comes inside on suits.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Eye irritation and scratching | eyes | EV08 |
| Skin abrasion and irritation, for example from gloves and under fingernails | hand | EV08 |
| Lung harm; the respiratory system is believed to be the most sensitive target | lungs | EV08 |
| Stuffy nose and sneezing | head | EV08 |
| Possible harm to the heart and lungs together (cardiopulmonary) | heart | EV19 |
| Possible allergy (research pending) | not one place | EV08 |
| Martian dust is likely toxic: highly oxidized, salty, with heavy metals and perchlorate | lungs | EV23 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Ordinary cabin dust from skin, hair and clothing lint; exercise adds more (EV01).
* **Deep-space transit:** As on a station. Dust from the Moon or Mars can float everywhere once back in weightlessness (EV08 on Apollo).
* **Lunar surface base:** Lunar dust limit applies. Lunar dust particles are mostly 0.02 to 5 µm (EV01). Apollo crews had eye irritation and nasal congestion (EV08). Low gravity keeps dust in the air longer (EV23).
* **Mars surface base:** Martian dust limit applies; it is stricter than the lunar limit, and peaks at ingress must stay below 10 mg/m3 (EV01).
* **Spacewalk or surface walk (activity overlay):** Surface walks bring dust back on suits. An airlock that separates the hatch from living areas helps (EV08).

**What is measured.** Mass of particles in the air by size: total dust, the respirable fraction (smaller than 2.5 µm) and lunar or Martian dust smaller than 10 µm. Main unit: mg/m3. Other units: µg/m3 (1 mg/m3 = 1000 µg/m3; low-cost sensors usually report µg/m3); particle counts per size bin.

**A low-cost sensor a hackathon team could bring.** An optical particle sensor (PM1, PM2.5, PM10) counts particles by how they scatter light and estimates their mass (EV33). **What it cannot tell:** It estimates mass from counts, so it cannot tell lunar or Martian dust from lint or skin flakes, and it assumes a particle density and shape. High humidity can cause inaccurate readings (EV32). It cannot see dust on skin or in eyes.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Cabin dust within the general limits: total dust below 3 mg/m3 and the respirable fraction (smaller than 2.5 µm) below 1 mg/m3. These do not apply to lunar or Martian dust. | 1 mg/m3 | continuous | EV01 |
| Keep an eye on it | Respirable cabin dust above 1 mg/m3, or dust rising after a surface walk. | 1 mg/m3 | continuous | EV01 |
| Act now | Lunar base: lunar dust smaller than 10 µm above 0.3 mg/m3 as a time-weighted average. | 0.3 mg/m3 | time-weighted average; intermittent daily exposure up to 6 months | EV01 |
| Act now | Mars base: Martian dust smaller than 10 µm above 0.1 mg/m3 as a time-weighted average. | 0.1 mg/m3 | time-weighted average; up to 30 days | EV01 |
| Emergency | Mars base: Martian dust above 10 mg/m3 at any moment, for example right after ingress. | 10 mg/m3 | instantaneous peak | EV01 |
| Emergency | Lunar base: NASA sets no short-term peak limit for lunar dust, so a peak alarm is a threshold to be set by clinical review. Raise Emergency for dust in an eye that does not clear with rinsing, or breathing trouble after dust exposure. | none | any moment | EV08 |

Exact quotes behind these levels:

* For your information (EV01): "dust to <3 mg/m3 with a crew generation rate of 1.33 mg/person-minute, and the respirable fraction of the total dust <2.5 μm (micrometer) in aerodynamic diameter to <1 mg/m3"
* Keep an eye on it (EV01): "They do not apply to reactive dust, e.g., lithium hydroxide (LiOH) or extraterrestrial dust."
* Act now (EV01): "below a time-weighted average of 0.3 mg/m3 during intermittent daily exposure periods that may persist up to 6 months in duration"
* Act now (EV01): "below a time-weighted average of 0.1 mg/m3 during exposure scenarios lasting up to 30 days in duration"
* Emergency (EV01): "Peak dust exposure levels should be limited to 10 mg/m3"
* Emergency (EV08): "acute limits for lunar dust are not being set at this time [...] ocular irritation occurred that required copious saline irrigation to treat"

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Brush off and contain suits in the airlock. Run the cabin cleaner. Apollo 12 crew kept helmets on until the cabin was clear (EV08).
* **Act now:** Wear a dust mask and goggles in the affected area (EV08 names these as controls). Vacuum. Log it for the ground.
* **Emergency:** Rinse the eyes with plenty of saline (EV08). Press Someone needs help for eye pain that does not clear or for any breathing trouble.

**Routine check.** continuous: NASA's example sampling rate for dust is every 1 minute (EV01 Table 6.1-1). per_eva: dust cleanup plus an eye, nose and skin check after each surface walk. daily: eye and nose irritation question on surface missions.

**Sources:** EV01, EV08, EV19, EV23, EV32, EV33.

<a id="noise"></a>

## 11. Noise

**In plain words.** Fans, pumps and machines run all the time. Long exposure can damage hearing and disturb sleep.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Hearing loss | ear | EV01, EV09 |
| Headaches | head | EV09 |
| Irritation | not one place | EV09 |
| Worse sleep and relaxation | not one place | EV09, C15 |
| Harder to talk and hear each other | ear | EV09 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Constant fan noise. ISS crews use acoustic monitors from time to time (EV01).
* **Deep-space transit:** Months of the same noise. Quiet sleep areas matter more: the stricter sleep limit applies on missions over 30 days (EV01).
* **Lunar surface base:** Life support machinery as on a station; same limits.
* **Mars surface base:** Life support machinery as on a station; same limits.
* **Spacewalk or surface walk (activity overlay):** Spacesuits have their own continuous noise requirement, outside this hazard (EV01).

**What is measured.** Sound level in work and sleep areas, and each person's daily noise exposure. Main unit: dBA. Other units: dB per octave band (for the NC curves); noise dose D (100 = the daily limit).

**A low-cost sensor a hackathon team could bring.** A sound level meter, or a sound meter app on a phone, reads dBA. A small MEMS microphone can log levels. **What it cannot tell:** One dBA number cannot check the NC-50 or NC-40 limits, which are set per octave band (EV01 Table 6.6-3). A fixed meter does not give each person's dose at the ear; NASA asks for per-person monitoring as needed (EV01). Phone microphones are not calibrated (team note).

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Work areas within the NC-50 curve; sleep areas within NC-40 on missions over 30 days. | 50 NC rating | continuous | EV01 |
| Keep an eye on it | Sleep area louder than the NC-40 curve on a mission over 30 days. | 40 NC rating | sleep period | EV01 |
| Act now | Daily noise dose above 100, which equals a 24-hour average of 70 dBA (or 75 dBA over 8 hours). | 70 dBA | 24 hours | EV01 |
| Emergency | Sound at the ear above 115 dBA, or sudden hearing loss, ringing or ear pain after a loud event. | 115 dBA | any moment | EV01 |

Exact quotes behind these levels:

* For your information (EV01): "limited to the values given by the Noise Criterion (NC)-50 curve"
* Keep an eye on it (EV01): "values given by the NC-40 curve"
* Act now (EV01): "corresponds to a 24-hour noise exposure level of 70 dBA"
* Emergency (EV01): "Noise levels above 115 dBA have been shown to produce noise-induced hearing loss"

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Use earplugs for sleep if they help; some crew sleep worse with them (EV01). Report noisy equipment.
* **Act now:** Move away from the source and use hearing protection. Hearing protection does not count toward meeting the limit, so the noise itself must be fixed (EV01). Log it for the ground.
* **Emergency:** Leave the noisy area. Press Someone needs help for sudden hearing loss. Log it for the ground.

**Routine check.** NASA asks for sound and personal noise monitoring 'as needed', and ISS crews measure periodically (EV01). Team default: a weekly check of work and sleep areas; frequency to be set by the flight surgeon. after_event: new or changed noisy equipment.

**Sources:** EV01, EV09, C15.

<a id="thermal-humidity"></a>

## 12. Temperature and humidity

**In plain words.** Cabin warmth and air moisture. Too hot, too cold, too dry or too damp harms comfort, skin and performance.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Dry, stuffy or irritated mucous membranes | not one place | EV01 |
| Skin rashes from microbial growth at high humidity | not one place | EV01 |
| Overheating and heat-related illness | not one place | EV01 |
| Worse performance from thermal stress | not one place | EV01 |
| Static electricity in very dry air (shock and ignition hazard) | not one place | EV01 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Controlled cabin. Crew can set the temperature in steps of 0.5 °C or smaller (EV01).
* **Deep-space transit:** As on a station.
* **Lunar surface base:** As on a station. On the lunar surface gravity adds convection, which changes air movement (EV01).
* **Mars surface base:** As on a station.
* **Spacewalk or surface walk (activity overlay):** The cabin limits do not apply in the suit; the suit has its own thermal requirements (EV01).

**What is measured.** Air temperature and relative humidity. Main unit: °C and % relative humidity. Other units: °F; dew point.

**A low-cost sensor a hackathon team could bring.** A digital temperature and humidity sensor. **What it cannot tell:** It reads one spot; hot pockets can form where air does not move (EV01, V2 6107). It cannot measure a person's body temperature or the heat stored in the body; that needs a thermometer.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Inside the comfort zone. NASA's rationale cites 20 to 25 °C with 30 to 60% humidity as supporting comfort and performance. The value is null because the zone is a temperature and humidity area, not one number. | none | continuous | EV01 |
| Keep an eye on it | Outside the comfort zone but inside the health limits zone. | none | continuous | EV01 |
| Act now | Outside the crew health limits zone. Its corners are 18 °C with 75% humidity and 27 °C with 25% humidity; average humidity stays above 25%. The full zone is a figure in NASA-STD-3001 Volume 2 (Figure 6.2-2), so the engine needs the figure's outline. Value null for that reason. | none | continuous | EV01 |
| Emergency | Anyone shows signs of heat illness or severe cold. Threshold to be set by clinical review. | none | any moment | EV01 |

Exact quotes behind these levels:

* For your information (EV01): "atmospheric parameters (20°C [...] 25°C and 30-60% humidity) to support human comfort and performance"
* Keep an eye on it (EV01): "Operating outside of this zone will cause performance decrements and impact productivity."
* Act now (EV01): "anchored at 18°C and 75% humidity on the upper left corner and 27 °C and 25% humidity on the lower right corner"
* Emergency (EV01): "The intent of this requirement is to protect the crewmember from heat-related illness."

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Adjust the temperature set point, your clothing or the local airflow.
* **Act now:** Tell the crew and the ground. In very dry air, drink extra water; NASA says low humidity calls for extra water to prevent dehydration (EV01).
* **Emergency:** Press Someone needs help. Move the person to a cooler or warmer module.

**Routine check.** continuous: NASA's example sampling rate for temperature and humidity is every 1 minute (EV01 Table 6.1-1).

**Sources:** EV01.

<a id="water-quality"></a>

## 13. Drinking water quality

**In plain words.** Drinking water is recycled from cabin humidity or brought from Earth. It must be free of harmful chemicals and germs, and taste good enough that you drink enough.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Nausea and headache from short contamination | abdomen | EV04 |
| Stomach and gut irritation | abdomen | EV04 |
| Kidney disease or cancer from long exposure | flank | EV04 |
| Drinking less because of bad taste or smell, leading to dehydration | not one place | EV04, EV01 |
| Infection from germs in water | abdomen | EV01 |
| Illness from too much iodine used as a disinfectant | not one place | EV01 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Recycled humidity condensate plus supplied water; iodine or silver is added as a disinfectant (EV01, EV10).
* **Deep-space transit:** NASA requires the capability to monitor water quality and alert the crew on board (EV01, V2 6046); far from Earth, problems must be handled with what is on board.
* **Lunar surface base:** As in transit. Any future local water must be tested before use.
* **Mars surface base:** As in transit. Local water may carry perchlorate (EV01).
* **Spacewalk or surface walk (activity overlay):** Suit drinking water comes from the same supply.

**What is measured.** Chemical contaminants (for example silver, ammonia, total organic carbon) and germs (bacteria count, coliforms). Main unit: mg/L (chemicals); CFU/mL (bacteria). Other units: ug/L.

**A low-cost sensor a hackathon team could bring.** Test strips and a conductivity (TDS) meter give rough chemistry; culture kits grow bacteria over days (team note). **What it cannot tell:** Strips and TDS meters do not identify the contaminants that have SWEG limits. Cultures take days. Neither can say the water is safe.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Total organic carbon at or below 5 mg/L (the 100 and 1000 day SWEG). | 5 mg/L | 100 to 1000 days | EV04 |
| Keep an eye on it | Silver above 0.4 mg/L (1000-day SWEG). | 0.4 mg/L | 1000 days | EV04 |
| Keep an eye on it | Ammonia above 1 mg/L (SWEG for 10 to 1000 days), or the water tastes or smells odd. | 1 mg/L | 10 to 1000 days | EV04 |
| Act now | Bacteria above 50 CFU/mL, or any coliform detected. | 50 CFU/mL | per sample | EV11 |
| Act now | Total organic carbon above 5 mg/L. | 5 mg/L | 100 to 1000 days | EV04 |
| Emergency | Several crew members sick to the stomach at once, or a water system alarm. Threshold to be set by clinical review. | none | any moment | EV01 |

Exact quotes behind these levels:

* For your information (EV04): "5 mg/L 5 mg/L TOC limit is based on reasonable worst"
* Keep an eye on it (EV04): "5 mg/L 5 mg/L 0.6 mg/L 0.4 mg/L Argyria is not considered an adverse toxic"
* Keep an eye on it (EV04): "5 mg/L 1 mg/L 1 mg/L 1 mg/L RWC resulting from unpleasant smell/taste."
* Act now (EV11): "Water 50 CFU/mL [...] No detectable coliforms"
* Emergency (EV01): "Microbially safe water is essential to prevent infection"

**What the app tells the crew**

* **For your information:** Nothing to do. Keep drinking enough.
* **Keep an eye on it:** Report any odd taste or smell. The ground reviews the last sample. Keep drinking enough water.
* **Act now:** Switch to a backup water source if there is one. Do not use the affected water for drinking or food. Log it for the ground.
* **Emergency:** Stop using that water. Press Someone needs help for anyone very sick. Log it for the ground.

**Routine check.** NASA says the water sampling rate depends on system design and mission (EV01 Table 6.1-1); frequency to be set by the flight surgeon. after_event: any change in taste or smell, or a new water source.

**Sources:** EV01, EV04, EV10, EV11.

<a id="microbial"></a>

## 14. Microbes in air and on surfaces

**In plain words.** Bacteria and fungi live wherever people live. In a closed cabin they grow on damp surfaces and spread through the air.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Infection | not one place | EV01 |
| Allergic reactions | not one place | EV01 |
| Microbes can become more able to cause disease in space | not one place | EV11 |
| Changes to the immune system in flight make infection more likely | not one place | EV11, EV29 |
| Microbes pass more easily between people in a closed habitat | not one place | EV29 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Routine in-flight sampling, with analysis on Earth (EV11).
* **Deep-space transit:** ISS monitoring relies partly on testing on Earth; for missions beyond, culture-independent on-board methods are being developed and in-flight monitoring is a listed countermeasure for lunar and Mars missions (EV11).
* **Lunar surface base:** As in transit. HEPA filters clean the air (EV01).
* **Mars surface base:** As in transit.
* **Spacewalk or surface walk (activity overlay):** Not applicable during the walk.

**What is measured.** Live bacteria and fungi in the air and on surfaces, as colony counts. Main unit: CFU/m3 (air); CFU/100 cm2 (surfaces). Other units: CFU/mL (water, see drinking water quality).

**A low-cost sensor a hackathon team could bring.** Agar contact or settle plates grow colonies that can be counted after days (team note; NASA grows samples on agar, EV11). **What it cannot tell:** Counts take days and do not say which organism it is or whether it causes disease.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Air bacteria at or below 1000 CFU/m3 and fungi at or below 100 CFU/m3 (ISS in-flight acceptability limits). | 1000 CFU/m3 | per sample | EV11 |
| Keep an eye on it | Visible mold, or damp surfaces and condensation that stay. Threshold to be set by clinical review. | none | any time | EV01 |
| Act now | Air bacteria above 1000 CFU/m3, or air fungi above 100 CFU/m3. | 1000 CFU/m3 | per sample | EV11 |
| Act now | Surface bacteria above 10,000 CFU/100 cm2, or surface fungi above 100 CFU/100 cm2. | 10,000 CFU/100 cm2 | per sample | EV11 |
| Emergency | Several crew members with fever or signs of infection at the same time. Threshold to be set by clinical review. | none | any time | EV01 |

Exact quotes behind these levels:

* For your information (EV11): "Air 1000 CFU/m3 100 CFU/m3"
* Keep an eye on it (EV01): "The system shall prevent condensation persistence on surfaces within the vehicle."
* Act now (EV11): "Surface 10,000 CFU/100 cm2 100 CFU/100 cm2"
* Emergency (EV01): "Microbiologically safe air is essential to prevent infection, allergic response"

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Dry and clean the area. Report any mold.
* **Act now:** Clean and disinfect the area as the procedure says, take a new sample, and log it for the ground.
* **Emergency:** Press Someone needs help. The medical officer decides on separating sick crew, as in the onboard procedure. Log it for the ground.

**Routine check.** NASA notes debate over routine testing versus testing only when someone is ill (EV11); frequency to be set by the flight surgeon. after_event: crew illness, visible mold, or a spill.

**Sources:** EV01, EV11, EV29.

<a id="microgravity"></a>

## 15. Weightlessness (microgravity)

**In plain words.** Floating without weight. Bones and muscles lose load, body fluids move toward the head, and the balance system gets confused.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Bone loss: weight-bearing bones lose on average 1% to 1.5% of mineral density per month | back | EV26, EV16 |
| Kidney stones from dehydration and calcium leaving the bones | flank | EV26 |
| Smaller, weaker muscles with less endurance | legs | EV22 |
| Fluids shift toward the head and may press on the eyes | eyes | EV26 |
| Eye changes (SANS): swelling of the optic disc, flattening of the back of the eye, folds, and a shift toward far-sightedness | eyes | S12 |
| Headaches from fluid shift that look like CO2 headaches | head | C25 |
| Space motion sickness, disorientation, and problems with balance, walking and fine motor control | ear | EV17 |
| Fainting or near-fainting when standing after return to gravity (orthostatic intolerance) | heart | C09 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Applies for the whole stay.
* **Deep-space transit:** Applies for months; NASA describes a six-month trip between Earth and Mars (EV26).
* **Lunar surface base:** Not on the surface; applies on the trip there and back.
* **Mars surface base:** Applies in transit; on arrival the body must readapt (EV26).
* **Spacewalk or surface walk (activity overlay):** Spacewalks in orbit are weightless; surface walks are in partial gravity.

**What is measured.** Nothing in the habitat measures this directly. It is a condition of the environment; the app tracks it through routine checks and self-reports.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Daily view of exercise done and fluids. With the exercise schedule kept and adequate equipment, ISS crews return with minimal muscle losses on average. | none | daily | EV22 |
| Keep an eye on it | Exercise sessions missed. How many: threshold to be set by clinical review. | none | per week | none |
| Act now | Strength test below 80% of the person's pre-flight baseline (NASA's in-mission target). | 80 % of pre-flight strength | per strength test | EV02 |
| Act now | Any new change in vision. A specialist eye examination is needed; threshold to be set by clinical review for what counts as a change. | none | any time | S12 |
| Emergency | Severe one-sided back or side pain (possible kidney stone), or fainting. | none | any time | EV26, C09 |

Exact quotes behind these levels:

* For your information (EV22): "if crew adhere to the exercise schedule and have access to adequate exercise countermeasure systems then on average, they return with minimal losses"
* Act now (EV02): "Countermeasures shall maintain in-mission skeletal muscle strength at or above 80% of baseline values."
* Act now (S12): "hyperopic shifts in refractive error observed in astronauts during and following long-duration spaceflight"
* Emergency (EV26, C09): "increased risk of developing kidney stones due to dehydration and increased excretion of calcium from their bones [...] It can result in presyncope and, ultimately, syncope"

**What the app tells the crew**

* **For your information:** Do your exercise sessions and drink enough water.
* **Keep an eye on it:** Make up missed exercise. Mention it at the next private medical conference.
* **Act now:** Tell the flight surgeon. A vision change needs a specialist eye examination (S12).
* **Emergency:** Press Someone needs help and follow its steps.

**Routine check.** daily: exercise and fluid log. Vision and strength checks: frequency to be set by the flight surgeon. mission_phase (return): fluid loading and care before standing (C09; EV01 gives fluid loading before reentry).

**Sources:** EV26, EV22, EV16, EV02, S12, C25, EV17, C09, EV01.

<a id="partial-gravity"></a>

## 16. Partial gravity (Moon and Mars)

**In plain words.** On the Moon and Mars you weigh less than on Earth. After weeks or months of floating, your body must readjust, and your bones and muscles still carry less load than at home.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Balance and orientation need time to adjust after landing, before hard tasks | ear | EV26 |
| Muscles, bones, inner ear and organs adjust to the new load | legs | N04 |
| Dizziness or fainting when standing after a long time weightless | heart | C09 |
| Dust stays in the air longer in low gravity | lungs | EV23 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Not applicable (weightless).
* **Deep-space transit:** Not applicable (weightless).
* **Lunar surface base:** About one sixth of Earth's gravity (EV01).
* **Mars surface base:** About three eighths of Earth's gravity (N04), after about six months weightless in transit (EV26).
* **Spacewalk or surface walk (activity overlay):** Surface walks happen in partial gravity.

**What is measured.** Nothing in the habitat measures this directly. It is a condition of the environment; the app tracks it through routine checks and self-reports.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | First days after landing: the readjustment period is shown on the dashboard. Its length: threshold to be set by clinical review. | none | after landing | EV26 |
| Keep an eye on it | Dizziness or unsteadiness when standing or walking after landing. | none | after landing | C09 |
| Act now | Still unsteady on a day with a planned surface walk or other critical task. Threshold to be set by clinical review. | none | before critical tasks | none |
| Emergency | Fainting (loss of consciousness). | none | any time | C09 |

Exact quotes behind these levels:

* For your information (EV26): "Astronauts may also need time to adjust to new gravity fields after landing on the Moon or Mars before they conduct mission-critical tasks."
* Keep an eye on it (C09): "It is triggered by upright positioning"
* Emergency (C09): "It can result in presyncope and, ultimately, syncope"

**What the app tells the crew**

* **For your information:** Take the readjustment time before hard tasks (EV26).
* **Keep an eye on it:** Sit or lie down until it passes. Tell a crewmate.
* **Act now:** Postpone the walk or critical task. Tell the flight surgeon.
* **Emergency:** Press Someone needs help.

**Routine check.** mission_phase (landing on the Moon or Mars): a daily dizziness and balance question during the readjustment period; its length to be set by the flight surgeon.

**Sources:** EV26, N04, EV01, C09, EV23.

<a id="isolation-confinement"></a>

## 17. Isolation and confinement

**In plain words.** Living for months in a small space with the same few people, far from family and friends. Stress builds up.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Stress, depression, anxiety, relationship problems and grief | not one place | C14 |
| These can pass with time or help, or develop into a psychiatric disorder | not one place | C14 |
| Sleep loss, body clock problems and work overload make it worse and can lower performance | not one place | EV27 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Months in a small crew, with the ground close at hand.
* **Deep-space transit:** Long and confined, with a growing communication delay (EV25).
* **Lunar surface base:** Small crew in a confined habitat, a few seconds of delay (EV01).
* **Mars surface base:** The most isolated: up to 20 minutes of delay (EV25) and a mission of roughly three years (N04).
* **Spacewalk or surface walk (activity overlay):** Not specific to walks.

**What is measured.** Nothing in the habitat measures this directly. It is a condition of the environment; the app tracks it through routine checks and self-reports.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Daily private mood check. It belongs to the person and is never shared without consent. | none | daily | C14 |
| Keep an eye on it | Mood check lower than the person's own baseline for several days. How many days: threshold to be set by clinical review. | none | rolling | none |
| Act now | Low mood or high stress on most days for a week, or a crewmate notices withdrawal. Threshold to be set by clinical review. | none | 7 days | none |
| Emergency | Any thought of self-harm or of harming someone else. | none | any time | C14 |

Exact quotes behind these levels:

* For your information (C14): "Assessing predictive and contributing factors related to behavioral health can help prevent the onset of distress."
* Emergency (C14): "The condition can be temporary, resolving with time and/or intervention, or can develop into a psychiatric disorder."

**What the app tells the crew**

* **For your information:** Take the private mood check. It is yours.
* **Keep an eye on it:** Talk to someone you trust. Keep your sleep and exercise routine.
* **Act now:** Book a private conference with the flight surgeon or psychologist.
* **Emergency:** Press Someone needs help. Stay with the person. Log it for the ground.

**Routine check.** daily: private mood check. Private medical conferences: frequency to be set by the flight surgeon (NASA says the flight surgeon schedules them, EV02).

**Sources:** C14, EV27, N04, EV25, EV02.

<a id="circadian-light"></a>

## 18. Body clock and light

**In plain words.** Your body clock follows light and dark. In space the light cycle and work hours are irregular, so the clock drifts out of step.

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| Tiredness | not one place | C23 |
| Thinking problems: inattention, distraction, slow reactions and poor decisions | head | C23 |
| Sleep disruption | not one place | C23 |
| Metabolic disturbance | not one place | C23 |
| Blue light before sleep holds back melatonin and makes sleep harder | eyes | EV18 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** Altered light and dark cycles and schedule shifts (C23).
* **Deep-space transit:** No natural day. Lighting sets the clock; missions will not necessarily follow a 24-hour day (EV18).
* **Lunar surface base:** Habitat lighting and the work schedule set the clock (EV18).
* **Mars surface base:** Habitat lighting and the work schedule set the clock (EV18).
* **Spacewalk or surface walk (activity overlay):** Long walks and their preparation shift work hours.

**What is measured.** Nothing in the habitat measures this directly. It is a condition of the environment; the app tracks it through routine checks and self-reports.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Daily sleep log and lighting schedule shown. | none | daily | C23 |
| Keep an eye on it | Short sleep, or a schedule shift coming. Threshold to be set by clinical review. | none | per night | none |
| Act now | Reaction test well below the person's own baseline before a critical task. Threshold to be set by clinical review. | none | before critical tasks | C23 |

Exact quotes behind these levels:

* For your information (C23): "Altered light cycles"
* Act now (C23): "cognitive impairment (e.g., inattention; distraction; slowed reaction time; poor decision making)"

**What the app tells the crew**

* **For your information:** Keep regular sleep times. Avoid blue light before sleep (EV18).
* **Keep an eye on it:** Protect your next sleep period. Follow the lighting schedule.
* **Act now:** Avoid critical tasks until rested. Tell the flight surgeon.
* **Emergency:** No emergency level for body clock problems. Use Someone needs help for any sudden illness.

**Routine check.** daily: sleep log. after_event (schedule shift): extra reaction test; frequency to be set by the flight surgeon.

**Sources:** C23, EV18, C15.

<a id="distance-autonomy"></a>

## 19. Distance from Earth (communication delay and autonomy)

**In plain words.** The farther from Earth, the longer messages take and the less the ground can help in time. On a Moon base at the South Pole the link also comes and goes, and on Mars it is cut for weeks at solar conjunction. The crew must act on its own: in Crew Health Check the ground is a reader of the log, never a step, and every plan aboard runs the same with the link up, delayed or down ([PRD.md](PRD.md) section 4.13).

**How it can affect the body**

| Effect | Body region | Sources |
|---|---|---|
| No direct effect on the body. Every medical event is harder because help and supplies come late or not at all | not one place | EV25 |
| Without the usual ground support, the crew may not be able to respond to unexpected critical failures | not one place | EV20 |

**How it shows up in each environment**

* **Space station, low Earth orbit:** The ground is close and supports in near real time.
* **Deep-space transit:** The delay grows every day; supplies are fixed at launch.
* **Lunar surface base:** 6 to 10 seconds of delay (EV01); a three-day trip home (N04).
* **Mars surface base:** Up to 20 minutes one way (EV25); about three years away from Earth (N04).
* **Spacewalk or surface walk (activity overlay):** Walks rely on the crew and the habitat, not on the ground.

**Line of sight, conjunction and the trip home.** These facts set the ground link the app shows for each environment and the return option of each plan. The quotes from N07 to N11 were read on 14 November 2026 and are not yet saved in research/environment/.

* **Lunar surface base at the South Pole.** Earth is "visible from the lunar South Pole for only about half of every sidereal month ... This will require deployment of orbiting relays to maintain continuous communications" (N07, p. 2). Without relays, direct-to-Earth coverage of South Pole landing sites is "43% best case and 17% worse case" (N08). The Moon base link is therefore down for long stretches between contacts, and the Habitat line shows the next contact; when the link is up, the delay is the 6 to 10 seconds above (EV01).
* **Mars surface base.** Crews would wait "between four and 24 minutes for their messages to reach mission control, and another four to 24 minutes to receive a response" (N09). Solar conjunction comes "approximately every 26 months" (N10) and cuts or degrades contact for one to several weeks: for the last one, NASA said it "will not have contact with any Mars missions" from 29 December 2025 to 16 January 2026 (N11).
* **The trip home.** "Rather than a three-day lunar trip, astronauts bound for Mars would be leaving our planet for roughly three years" (EV25). A peer-reviewed review adds that evacuation from the Moon back to Earth "may take as long as 2 weeks" ([Levin and others, 2023](https://pmc.ncbi.nlm.nih.gov/articles/PMC10810302/)). Only the Moon base has a return option in the app, and the commander and the medical officer decide; Mars and transit plans say "Return is not an option from here. The plan stays aboard."
* **Care aboard is the standard.** NASA-STD-3001 Volume 1 Revision C asks programs for in-mission care that includes "Means of providing autonomous medical care and advanced life support" [V1 3004], and explains that "a return to Earth may not be possible, or may take such a long time as to be rendered futile" [V1 3007, rationale] (EV02). It asks programs to "train a minimum of two crewmembers per vehicle/platform as Crew Medical Officers (CMOs)" [V1 3006] (EV02). The standard no longer has Levels of Care: "The Levels of Care have been replaced with Health and Medical Care Technical requirements" (EV02, document history), so the app cites these requirements, never a level. NASA's medical operations brief (RS68, OCHMO-TB-044) is cited only for "Program designates/trains two CMOs for each increment."; it says nothing about delay, Earth independence or return times.

**What is measured.** Nothing in the habitat measures this directly. It is a condition of the environment; the app tracks it through routine checks and self-reports.

**Limits and warning levels**

| Level | When | Value | Window | Sources |
|---|---|---|---|---|
| For your information | Show the current one-way delay and when the ground last answered. | none | continuous | EV25 |
| Keep an eye on it | Ground out of contact longer than planned. How long: threshold to be set by clinical review. | none | continuous | none |
| Act now | A medical problem while the ground cannot answer in time: follow on-board steps and log everything to send later. | none | any time | EV02 |
| Emergency | Someone needs help and the ground cannot answer in time. Alerts and fixed steps never wait for the ground. | none | any time | EV01 |

Exact quotes behind these levels:

* For your information (EV25): "communication delay of up to 20 minutes one way"
* Act now (EV02): "when missions have communication delays, different modalities can be considered (e.g., stored/forward communications)"
* Emergency (EV01): "The crew must be alerted even if ground communication is disrupted or is unviable."

**What the app tells the crew**

* **For your information:** Nothing to do.
* **Keep an eye on it:** Note the time. Keep logging. Plan as if no answer will come soon.
* **Act now:** Use the on-board steps. Record what you did and send it when the link returns.
* **Emergency:** Press Someone needs help. Its steps never wait for the ground.

**Routine check.** continuous: delay and last-contact time on screen. mission_phase (pre-flight): practice with delayed communication, as NASA's HERA analog crews do (EV25).

**Sources:** EV25, EV20, EV01, EV02, N04, N07, N08, N09, N10, N11, RS68.

**About EV20.** NASA's EIHSO risk page is about crews operating the vehicle and habitat systems without ground support: the risk applies "where the crew-members are expected to interact with the vehicle in both nominal and off-nominal operation" (EV20). It is cited here for that, never as a medical source; the case for medical care aboard rests on EV02 and EV25.

## What could not be sourced

* Radiation alert thresholds for dose rate. NASA requires the program to predefine them (EV01) but gives no number. The watch and act levels wait for clinical review.
* An emergency CO2 level. NASA-STD-3001 leaves off-nominal CO2 values to each program (EV01).
* A short-term peak limit for lunar dust. NASA says acute limits are not being set at this time (EV08).
* A spacecraft limit for perchlorate in air, water or food. The saved water guidelines (EV04) have no perchlorate entry; for air, NASA relies on the Martian dust limit (EV01).
* Body temperature thresholds for heat illness or cold, and the outline of the crew health temperature and humidity zone, which exists only as a figure (EV01, Figure 6.2-2).
* In-flight microbial limits beyond the ISS acceptability limits quoted in NASA's microbiology brief (EV11). That brief notes debate over routine versus as-needed testing.
* A communication delay figure for low Earth orbit, and the length of a Mars day. Neither appears in the sources read.
* The length of each planned contact gap at a given South Pole site. It depends on the site and on relays (N08); the demo uses a Sim contact schedule.
* Criteria for returning a crew member from the Moon. Flight rules are not public; plans read "[to be set by flight rules]".
* How often to sample water and microbes, read personal dosimeters, check vision or strength, and hold private conferences. These are marked "frequency to be set by the flight surgeon".
* What a low-cost Geiger counter, phone sound meter, water test strip or culture plate reads in space. The limitations given are partly team notes.
* The PIO2 formula in NASA-STD-3001 Volume 2 Table 6.2-1 did not extract cleanly as text; read it from the PDF before coding the oxygen conversion.

## Sources

All sources are public. Saved texts are plain text extracted from the downloaded PDF or web page; PDF text keeps page markers (`=== page N ===`).

| ID | Title | Date | Access | Saved file |
|---|---|---|---|---|
| EV01 | [NASA-STD-3001 Volume 2, Revision F: Human Factors, Habitability, and Environmental Health](https://standards.nasa.gov/system/files/tmp/NASA-STD-3001%20Vol%202%20Rev%20F.pdf) | 2026-07-14 (approved) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV01.txt](research/environment/EV01.txt) |
| EV02 | [NASA-STD-3001 Volume 1, Revision C: Crew Health](https://standards.nasa.gov/sites/default/files/standards/NASA/C/nasa-std-3001-vol-1-rev-c-signature.pdf) | 2023-09-15 (approved) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV02.txt](research/environment/EV02.txt) |
| EV03 | [JSC 20584 Revision D, Spacecraft Maximum Allowable Concentrations for Airborne Contaminants](https://www.nasa.gov/wp-content/uploads/2026/02/jsc-20584-smacs-rev-d.pdf) | February 2026 | Public PDF, retrieved 2026-11-14 UTC. The SMAC table pages (9 to 16) are images: their text was read by OCR and kept only as a cross-check | [research/environment/EV03.txt](research/environment/EV03.txt) |
| EV04 | [JSC 63414 Revision A, Spacecraft Water Exposure Guidelines (SWEGs)](https://www.nasa.gov/wp-content/uploads/2024/02/jsc-63414-swegs-rev-a.pdf) | November 2023 | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV04.txt](research/environment/EV04.txt) |
| EV05 | [OCHMO-TB-020 Design for Ionizing Radiation Protection, Rev G (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-020-radiation-protection.pdf) | 2025-06-06 (Rev G) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV05.txt](research/environment/EV05.txt) |
| EV06 | [OCHMO-TB-003 Habitable Atmosphere, Rev A (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-003-habitable-atmosphere.pdf) | 2023-11-30 (Rev A) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV06.txt](research/environment/EV06.txt) |
| EV07 | [OCHMO-TB-015 Spaceflight Toxicology, Rev B (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-015-spaceflight-toxicology.pdf) | 2023-10-30 (Rev B) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV07.txt](research/environment/EV07.txt) |
| EV08 | [OCHMO-TB-011 Lunar Dust, Rev C (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-011-lunar-dust-technical-brief.pdf) | 2023-11-28 (Rev C) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV08.txt](research/environment/EV08.txt) |
| EV09 | [OCHMO-TB-035 Acoustics, Rev C (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-035-acoustics.pdf) | 2023-10-06 (Rev C) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV09.txt](research/environment/EV09.txt) |
| EV10 | [OCHMO-TB-027 Water, Rev C (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-027-water.pdf) | 2023-11-29 (Rev C) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV10.txt](research/environment/EV10.txt) |
| EV11 | [OCHMO-TB-046 Microbiology in Space Overview, Rev A (technical brief)](https://www.nasa.gov/wp-content/uploads/2025/05/ochmo-tb-046-microbiology-rev-a.pdf) | 2025-05-19 (Rev A) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV11.txt](research/environment/EV11.txt) |
| EV12 | [OCHMO-TB-037 Decompression Sickness (DCS) Risk Mitigation, Revision D (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-037-decompression-sickness.pdf) | Revision D; posted December 2023 (URL path) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV12.txt](research/environment/EV12.txt) |
| EV14 | [OCHMO-TB-008 Fire Protection, Rev A (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-008-fire-protection.pdf) | 2023-11-29 (Rev A) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV14.txt](research/environment/EV14.txt) |
| EV15 | [Nominal Spacesuit Carbon Dioxide Levels Standard Technical Bulletin](https://www.nasa.gov/wp-content/uploads/2025/02/suited-co2-technical-bulletin-02182025.pdf) | 2025-02-18 | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV15.txt](research/environment/EV15.txt) |
| EV16 | [OCHMO-TB-030 Bone Loss, Revision D (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-030-bone-loss.pdf) | Revision D; posted December 2023 (URL path) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV16.txt](research/environment/EV16.txt) |
| EV17 | [OCHMO-TB-010 Sensorimotor, Rev A (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-010-sensorimotor.pdf) | 2023-12-01 (Rev A) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV17.txt](research/environment/EV17.txt) |
| EV18 | [OCHMO-TB-026 Lighting Design, Rev C (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-026-lighting-design.pdf) | 2023-12-19 (Rev C) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV18.txt](research/environment/EV18.txt) |
| EV19 | [Risk of Adverse In-Mission Health and Performance Effects and Long-Term Health Effects Due to Celestial Dust Exposure (Dust Risk), NASA risk page](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-adverse-health-performance-effects-of-celestial-dust-exposure/) | Page last updated 2025-09-17 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV19.txt](research/environment/EV19.txt) |
| EV20 | [Risk of Adverse Mission Outcomes Due to Earth Independent Human-Systems Operations (EIHSO Risk), NASA risk page](https://www.nasa.gov/directorates/esdmd/hhp/eihso-risk/) | Page last updated 2025-09-12 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV20.txt](research/environment/EV20.txt) |
| EV21 | [Risk of Reduced Crew Health and Performance Due to Hypoxia, NASA risk page](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-reduced-crew-health-and-performance-due-to-hypoxia/) | Page last updated 2025-06-24 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV21.txt](research/environment/EV21.txt) |
| EV22 | [Risk of Reduced Physical Performance Capabilities Due to Reduced Muscle Size, Strength, and Endurance (Muscle Risk), NASA risk page](https://www.nasa.gov/directorates/esdmd/hhp/muscle-risk/) | Page last updated 2025-08-20 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV22.txt](research/environment/EV22.txt) |
| EV23 | [Evidence Report: Risk of Adverse Health and Performance Effects of Celestial Dust Exposure (Human Research Program)](https://humanresearchroadmap.nasa.gov/Evidence/reports/Dust.pdf) | 2015-08-04 (approved for public release) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/EV23.txt](research/environment/EV23.txt) |
| EV24 | [JSC 20584 Revision C, Spacecraft Maximum Allowable Concentrations for Airborne Contaminants](https://ntrs.nasa.gov/api/citations/20240007713/downloads/JSC%2020584_SMACs_Rev%20C_FINAL.pdf) | 2024-06-13 | Public PDF, full text, retrieved 2026-11-14 UTC. Superseded by Rev D (EV03); used because its table extracts as text. The extracted table rows list values out of column order (see the note on reading SMAC rows) | [research/environment/EV24.txt](research/environment/EV24.txt) |
| EV25 | [Hazard: Distance from Earth, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-distance-from-earth/) | Page last updated 2024-08-01 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV25.txt](research/environment/EV25.txt) |
| EV26 | [Hazard: Gravity Fields, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-gravity-fields/) | Page last updated 2024-08-01 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV26.txt](research/environment/EV26.txt) |
| EV27 | [Hazard: Isolation and Confinement, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-isolation-and-confinement/) | Page last updated 2024-08-01 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV27.txt](research/environment/EV27.txt) |
| EV28 | [Hazard: Space Radiation, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-space-radiation/) | Page last updated 2025-06-05 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV28.txt](research/environment/EV28.txt) |
| EV29 | [Hazard: Hostile/Closed Environments, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-hostile-closed-environments/) | Page last updated 2024-08-01 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV29.txt](research/environment/EV29.txt) |
| EV30 | [ToxFAQs for Perchlorates, ATSDR (CDC)](https://wwwn.cdc.gov/TSP/ToxFAQs/ToxFAQsDetails.aspx?faqid=893&toxid=181) | Page last reviewed 2014-03-25 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV30.txt](research/environment/EV30.txt) |
| EV31 | [Volatile Organic Compounds' Impact on Indoor Air Quality, US EPA](https://www.epa.gov/indoor-air-quality-iaq/volatile-organic-compounds-impact-indoor-air-quality) | Last updated 2026-06-23 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV31.txt](research/environment/EV31.txt) |
| EV32 | [Frequent Questions about Air Sensors, US EPA Air Sensor Toolbox](https://www.epa.gov/air-sensor-toolbox/frequent-questions-about-air-sensors) | Last updated 2026-09-29 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV32.txt](research/environment/EV32.txt) |
| EV33 | [Evaluation of Emerging Air Sensor Performance, US EPA Air Sensor Toolbox](https://www.epa.gov/air-sensor-toolbox/evaluation-emerging-air-sensor-performance) | Last updated 2026-09-29 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/EV33.txt](research/environment/EV33.txt) |
| C09 | [OCHMO-TB-019 Orthostatic Intolerance, Rev B (technical brief; package ID from DEMO-CONDITIONS.md)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-019-orthostatic-intolerance.pdf) | 2023-12-01 (Rev B) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/C09.txt](research/environment/C09.txt) |
| C14 | [OCHMO-TB-016 Behavioral Health and Performance, Rev B (technical brief; package ID)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-016-behavioral-health.pdf) | 2023-10-24 (Rev B) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/C14.txt](research/environment/C14.txt) |
| C15 | [OCHMO-TB-041 Sleep Accommodations, Rev B (technical brief; package ID)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-041-sleep-accommodations.pdf) | 2023-10-23 (Rev B) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/C15.txt](research/environment/C15.txt) |
| C23 | [OCHMO-MTB-009 Circadian Desynchrony (medical technical brief; package ID)](https://www.nasa.gov/wp-content/uploads/2026/03/ochmo-mtb-009-circadian-desynchrony.pdf) | Posted March 2026 (URL path) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/C23.txt](research/environment/C23.txt) |
| C25 | [OCHMO-TB-004 Carbon Dioxide, Revision D (technical brief; package ID)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-004-carbon-dioxide.pdf) | Revision D; posted December 2023 (URL path) | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/C25.txt](research/environment/C25.txt) |
| S12 | [OCHMO-MTB-001 Spaceflight Associated Neuro-ocular Syndrome (SANS) (medical technical brief; package ID)](https://www.nasa.gov/wp-content/uploads/2024/02/ochmo-mtb-001-sans.pdf) | 2024-02-21 | Public PDF, full text, retrieved 2026-11-14 UTC | [research/environment/S12.txt](research/environment/S12.txt) |
| N04 | [5 Hazards of Human Spaceflight, NASA Human Research Program (package ID)](https://www.nasa.gov/hrp/hazards/) | Page last updated 2026-06-12 | Public web page, text saved, retrieved 2026-11-14 UTC | [research/environment/N04.txt](research/environment/N04.txt) |
| N07 | [NASA Lunar Communications and Navigation Architecture (package ID)](https://www.nasa.gov/wp-content/uploads/2024/01/lunar-communications-and-navigation-architecture.pdf) | Posted January 2024 (URL path) | Public PDF, read 14 November 2026 | Not yet saved |
| N08 | [Lunar Communications Relay and Navigation Systems, SpaceOps 2025 (package ID)](https://ntrs.nasa.gov/api/citations/20250003321/downloads/LCRNS_Esper_257.pdf) | 2025 | Public PDF, read 14 November 2026 | Not yet saved |
| N09 | [Space Communications: 7 Things You Need to Know, NASA (package ID)](https://www.nasa.gov/missions/tech-demonstration/space-communications-7-things-you-need-to-know) | Not recorded | Public web page, read 14 November 2026 | Not yet saved |
| N10 | [Communication Delays, Disruptions, and Blackouts for Crewed Mars Missions, NASA Langley, ASCEND 2022 (package ID)](https://ntrs.nasa.gov/api/citations/20220013418/downloads/ASCEND-Communication%20Delays%2C%20Disruptions%2C%20and%20Blackouts%20for%20Crewed%20Mars%20Missions.pdf) | 2022 | Public PDF, read 14 November 2026 | Not yet saved |
| N11 | [NASA works MAVEN spacecraft issue ahead of solar conjunction, NASA MAVEN blog (package ID)](https://science.nasa.gov/blogs/maven/2025/12/23/nasa-works-maven-spacecraft-issue-ahead-of-solar-conjunction/) | 23 December 2025 | Public web page, read 14 November 2026 | Not yet saved |
