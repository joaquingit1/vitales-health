# NASA documents and data

Every NASA document and dataset the research found, in one place. Section 1 lists the 96 NASA documents the product actually uses: first the core ones and what each does in it, then those behind the plans aboard and the ground link, then those behind the habitat, the routine and Someone needs help. With the NASA data sources and known-outcome case documents at the end of section 1 and the data in section 2, that is every NASA resource in the product. Section 2 is the NASA data that ships in this package. Section 3 is NASA work close to ours. Section 4 holds the rules for using NASA material. Section 5 is the full catalog of 580 documents the research found, grouped by kind.

Source IDs match the rest of the package: S IDs are in [sources.csv](sources.csv), C IDs in [DEMO-CONDITIONS.md](DEMO-CONDITIONS.md), EV IDs in [ENVIRONMENTS.md](ENVIRONMENTS.md), RT IDs in [ROUTINE.md](ROUTINE.md), RS IDs in [RESPONDER-FLOWS.md](RESPONDER-FLOWS.md), and N IDs are introduced on this page. The [research annex](RESEARCH-ANNEX.md) explains how the sources were found and how far each one can be trusted.

## 1. NASA documents the product uses

| ID | Document | What it is | What it does in the product |
|---|---|---|---|
| S01 | [ExMC Evidence Library Methods, HRP-48036 Rev B](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf) | The 119 active conditions of NASA's Integrated Medical Model condition list (ICL 1.0 v3.2) and the broader inventories, 792 rows | The rows of the kit map: for each of the 119 conditions, the test that would confirm it and whether this ship's kit carries it, written at the event from the Clinical Findings Forms (S13). Also the condition registry behind the assistant's requests and its "not available here" answers, the [condition browser](condition-browser.html) and every [demo condition](DEMO-CONDITIONS.md) |
| N01 | [NASA-STD-3001 Volume 2, Rev F](https://standards.nasa.gov/system/files/tmp/NASA-STD-3001%20Vol%202%20Rev%20F.pdf), section 6.2.1.3, requirement V2 6004 | The crew habitat standard, approved 14 July 2026. V2 6004 keeps the nominal one-hour average ppCO2 at no more than 3 mmHg and leaves off-nominal values to mission requirements | The dotted CO2 limit on the Habitat card, and the source of the other habitat limits (EV01). An environmental requirement, never an individual medical threshold |
| S02 | [NASA-STD-3001 Appendix E](https://www.nasa.gov/reference/appendix-e-health-and-medical-care-technical-requirements/) | Health and medical care technical requirements | The medical capability categories behind the condition families |
| N02 | [OSDR Environmental Data Application](https://osdr-tutorials.readthedocs.io/en/latest/pages/guides/environmental_data_application.html), [Rodent Research-1 dashboard](https://visualization.osdr.nasa.gov/eda/dashboard_RR-1) | ISS cabin telemetry from the RR-1 mission (SpaceX-4, 2014): temperature, humidity and CO2 | The CO2 reading on the Habitat card replays a week of it, labeled Replay with its original dates. Data in section 2 |
| N19 | [Space Station Facts and Figures, NASA](https://www.nasa.gov/international-space-station/space-station-facts-and-figures/) | NASA's fact page for the ISS: "Habitable Volume: 13,696 cubic feet (388 cubic meters) not including visiting vehicles" | The cabin air volume of the simulated station in `simulators/habitat/`, which sets how fast CO2, ammonia and smoke build up |
| N20 | [OSDR RadLab](https://visualization.osdr.nasa.gov/radlab/), [data API documentation](https://visualization.osdr.nasa.gov/radlab/gui/data-api/) | Radiation instrument records from NASA's Open Science Data Repository; the DosTel absorbed dose rate is in µGy per hour | The source of radlab_dostel_sample.csv (section 2): the station's replayed dose rate, and the background level and variability of the simulated area monitor. Also the source of radlab_iss_2017-09_spe.csv and the MSL-RAD surface file of the September 2017 cases: the replayed Columbus (DosTel2) and Mars surface dose rates, and the shape of the simulated solar particle event |
| C25 | [OCHMO-TB-004 Carbon Dioxide, Rev D](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-004-carbon-dioxide.pdf) | Technical brief: CO2 physiology, localized exposure and headache evidence | What the CO2 warning says it can do and what to do, and the CO2 headache entry (ICL64) |
| C15 | [OCHMO-TB-041 Sleep Accommodations, Rev B](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-041-sleep-accommodations.pdf) | Technical brief: sleep opportunity, circadian alignment, environment, work and rest | The sleep card and the sleep entries (ICL95, ICL18) |
| C23 | [OCHMO-MTB-009 Circadian Desynchrony](https://www.nasa.gov/wp-content/uploads/2026/03/ochmo-mtb-009-circadian-desynchrony.pdf) | Medical technical brief, March 2026 | Sleep and reaction-time context (ICL95, ICL18) |
| C14 | [OCHMO-TB-016 Behavioral Health and Performance, Rev B](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-016-behavioral-health.pdf) | Technical brief | The mood card and the anxiety entry (ICL14) |
| C09 | [OCHMO-TB-019 Orthostatic Intolerance, Rev B](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-019-orthostatic-intolerance.pdf) | Technical brief: fluid shift and baroreflex changes in flight | "What spaceflight does to it" on the pulse card (ICL10) |
| S12 | [OCHMO-MTB-001 Spaceflight Associated Neuro-ocular Syndrome](https://www.nasa.gov/wp-content/uploads/2024/02/ochmo-mtb-001-sans.pdf) | Medical technical brief, February 2024 | The eye entry (ICL100) and its "needs specialist eye examination" answer |
| S13 | [Integrated Medical Model documentation](https://www.nasa.gov/integrated-medical-model-documentation/) | The model's documentation and its Clinical Findings Forms | The Clinical Findings Forms, one per condition: the confirming test and body region of each kit map row, linked from the row and from every "Not available here" line. The forms model a physician aboard, so the kit map uses them for what would confirm a condition, never for steps a non-doctor follows. Listed in section 5 |
| N06 | [NASA Integrated Medical Model (IMM) overview](https://www.nasa.gov/nasa-integrated-medical-model-imm-overview/) | What the model is for: it helps planners "optimize the medical resources packed" for missions, and under "Clinical Optimism" it "assumes that all medical conditions are correctly diagnosed by the crew" | The question the kit map answers from the crew's side: with this kit, can they? |
| S14 | [Human Research Roadmap evidence](https://humanresearchroadmap.nasa.gov/Evidence/) | Risk evidence reports and knowledge gaps | Why each measurement matters, on the measurement cards |
| N03 | [Behavioral health evidence report, 2025](https://humanresearchroadmap.nasa.gov/evidence/reports/HRP-ER-BMED-HFBP-20250210.pdf) | Separates adverse behavioral changes from psychiatric disorders | Keeps the mood check a private self-report and rules out claims such as "depression detected" |
| N04 | [The five hazards of human spaceflight](https://www.nasa.gov/hrp/hazards/) | NASA's summary of the hazards | What the product tracks for each hazard and what it marks "not measured by this kit" |
| N05 | [OCHMO technical brief index](https://www.nasa.gov/ochmo/hsa-standards/ochmo-technical-briefs/) | Every NASA technical and medical brief | Where to find the brief for a new measurement card |

### NASA documents behind the plans aboard and the ground link

Read on 14 November 2026 for the plan aboard ([PRD](PRD.md) section 4.13). They set the ground link's schedule for each environment and show why no plan step can wait for the ground. None of them is a step the product waits on.

| ID | Document | What it is | What it does in the product |
|---|---|---|---|
| N07 | [Lunar Communications and Navigation Architecture](https://www.nasa.gov/wp-content/uploads/2024/01/lunar-communications-and-navigation-architecture.pdf), p. 2 | NASA's lunar communications architecture: Earth is "visible from the lunar South Pole for only about half of every sidereal month" | The Moon base's Sim contact schedule and the next contact on the Habitat line ([ENVIRONMENTS.md](ENVIRONMENTS.md), section 19) |
| N08 | [Lunar Communications Relay and Navigation Systems, SpaceOps 2025](https://ntrs.nasa.gov/api/citations/20250003321/downloads/LCRNS_Esper_257.pdf), pp. 1 and 7 | Without relays, direct-to-Earth coverage of South Pole landing sites is "43% best case and 17% worse case" | The planned line-of-sight gap on the Moon base, and its gap kind |
| N09 | [Space Communications: 7 Things You Need to Know](https://www.nasa.gov/missions/tech-demonstration/space-communications-7-things-you-need-to-know) | NASA explainer: Mars messages take "between four and 24 minutes" to reach mission control, and as long again for the reply | The Mars base's delay when the link is up |
| N10 | [Communication Delays, Disruptions, and Blackouts for Crewed Mars Missions, NASA Langley, 2022](https://ntrs.nasa.gov/api/citations/20220013418/downloads/ASCEND-Communication%20Delays%2C%20Disruptions%2C%20and%20Blackouts%20for%20Crewed%20Mars%20Missions.pdf) | Solar conjunction "approximately every 26 months", with disruption depending on the band | The Mars base's conjunction gap: contact lost or degraded for one to several weeks |
| N11 | [NASA MAVEN blog, 23 December 2025](https://science.nasa.gov/blogs/maven/2025/12/23/nasa-works-maven-spacecraft-issue-ahead-of-solar-conjunction/) | NASA "will not have contact with any Mars missions" from 29 December 2025 to 16 January 2026 | A recent conjunction gap, as an example of its length |
| EV02, RT02 | [NASA-STD-3001 Volume 1, Revision C](https://standards.nasa.gov/sites/default/files/standards/NASA/C/nasa-std-3001-vol-1-rev-c-signature.pdf): [V1 3004], [V1 3006], [V1 3007] | In-mission care includes "Means of providing autonomous medical care and advanced life support" [V1 3004]; at least two crew medical officers [V1 3006]; "a return to Earth may not be possible, or may take such a long time as to be rendered futile" [V1 3007, rationale]. The standard replaced its Levels of Care with technical requirements | Why every plan runs aboard, why the roster has a second medical officer, and why only the Moon base has a return stage. Cited by requirement, never by a level |
| N12 | [NASA-STD-3001 Volume 1, Revision A (historical)](https://standards.nasa.gov/sites/default/files/standards/NASA/B/Historical/NASA-STD-3001-VOL-1A-Chg1-1.pdf) | The earlier revision with Levels of Care, including one for missions where "Return to Earth is not a viable option", with care "at the physician level" | History only, on the project page: NASA once planned a physician for long autonomous missions; the product covers the case when the medical officer is the patient or away. Never cited as a current requirement |
| N13 | [NASA ExMC, Earth Independent Medical Operations, AsMA 2023](https://ntrs.nasa.gov/api/citations/20230007863/downloads/EIMO.AsMA2023.Lemery.final.pdf), slides 3 and 5 | Exploration missions: "No real-time communications + blackouts", "No evacuations possible", "Expanded onboard medical care (crew/vehicle-reliant)" | The problem the plan aboard answers, on the pitch and in the PRD |
| N14 | [A Clinical Decision Support System for Earth Independent Medical Operations, NASA Ames, 2024](https://www.nasa.gov/centers-and-facilities/ames/ames-science/ames-space-biosciences/a-clinical-decision-support-system-for-earth-independent-medical-operations/) | "Deep space exploration crews will need to autonomously detect, diagnose, treat, and prevent medical conditions" | The Impact argument on the pitch |
| N15 | [NASA abstract on crew medical officer training, AsMA 2021](https://ntrs.nasa.gov/citations/20205008925) | "approximately 15 hours total is spent on CMO training", "knowing that ground specialists will provide real-time guidance" | The gap that teach and check fills aboard |
| N16 | [NASA abstract on AI-guided ultrasound, AsMA 2026](https://ntrs.nasa.gov/citations/20260001797) | AI guidance for ultrasound that cuts "the need for direct Earth-based intervention" | Where NASA's own work is going; the pitch positions the product beside it, not against it |
| N17 | [NASA ADUM briefing](https://www.nasa.gov/sites/default/files/files/issrdc_2012-06-26-1000_melton2012.pdf), slide 13 | "minimal crew training, just-in-time training and remote expert guidance, can be successfully used to complete complex diagnostic ultrasound exams" | The precedent for teaching a non-physician in flight; teach and check puts the camera check where the remote expert was |
| N18 | [Shuttle Medical Checklist, JSC-48031 Rev K (archived NASA copy)](http://web.archive.org/web/20170212012224/https://www.nasa.gov/centers/johnson/pdf/177788main_MedicalCklist.pdf), p. 6-2 | NASA's own copy of the Loss of Signal rule that RS08 quotes from a third-party mirror of the ISS checklist: begin treatment first, then call the surgeon | Independent NASA confirmation that care starts without the ground |

### NASA documents behind the simulated clinical devices

Read on 14 November 2026 for the simulated twins of the kit oximeter, cuff and thermometer (`simulators/clinical/`, AGENTS.md section 4). They set the twins' resting heart rate and the timing of the body temperature rhythm. None of them is a clinical threshold, and no adapter check depends on them.

| ID | Document | What it is | What it does in the product |
|---|---|---|---|
| N21 | [Human muscle sympathetic nerve activity and plasma noradrenaline kinetics in space, Ertl and others, J Physiol 2002 (Neurolab), NTRS 20040088546](https://ntrs.nasa.gov/citations/20040088546), abstract | Resting heart rate of 56 +/- 4 beats per minute (mean +/- SEM), similar before flight and in flight | The twins' mean resting heart rate |
| N22 | [Risk of Performance Decrements and Adverse Health Outcomes Resulting from Sleep Loss, Circadian Desynchronization, and Work Overload, HRP evidence report, 2016, NTRS 20160003864](https://ntrs.nasa.gov/api/citations/20160003864/downloads/20160003864.pdf), p. 13 | A circadian rhythm of core body temperature in flight, with "an average phase delay of two hours" in three of four Mir astronauts; no amplitude | That the thermometer twin's temperature follows a daily rhythm, and that a scenario may shift its phase |
| N23 | [Effects of 9-hour time zone changes on fatigue and circadian rhythms of sleep/wake and core temperature, NASA TM-88197, Gander and others, 1985, NTRS 19860007420](https://ntrs.nasa.gov/api/citations/19860007420/downloads/19860007420.pdf), p. 9 | "The average baseline acrophase of the temperature rhythm ... was 15.2 hr" (Greenwich Mean Time, aircrew on Earth) | The hour of the thermometer twin's daily temperature peak |

Non-NASA exceptions for the same twins and adapters, used only because no catalog document covers them: the resting SpO2 mean and the rule that an oximeter reading counts once it "stops changing and displays one steady number" (C08, FDA Pulse Oximeter Basics, quoted in DEMO-CONDITIONS.md and ROUTINE.md section 5); the resting blood pressure means (RT27, MedlinePlus Blood pressure measurement, ROUTINE.md section 4); the 37 C temperature mesor ([MedlinePlus, Body temperature norms](https://medlineplus.gov/ency/article/001982.htm)); and the device protocols (Bluetooth SIG GATT specifications for the PLX 0x1822, BPS 0x1810 and HTS 0x1809 services). No source read gives a daily temperature amplitude or a device range or rate limit, so the twin leaves the amplitude out and the adapter profiles leave those checks unset and say so in each reading's quality notes.

### NASA documents behind the habitat, the routine and Someone needs help

These 75 NASA documents carry the habitat limits and adverse effects, the routine's frequencies and the spaceflight parts of the first-aid steps. ENVIRONMENTS.md, ROUTINE.md and RESPONDER-FLOWS.md quote them exactly and keep the saved text under [research/](research/). A document used in more than one of them has one ID in each.

| IDs | Document | Used for |
|---|---|---|
| EV01, RT03 | [NASA-STD-3001 Volume 2, Revision F: Human Factors, Habitability, and Environmental Health](https://standards.nasa.gov/system/files/tmp/NASA-STD-3001%20Vol%202%20Rev%20F.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |
| EV02 | [NASA-STD-3001 Volume 1, Revision C: Crew Health](https://standards.nasa.gov/sites/default/files/standards/NASA/C/nasa-std-3001-vol-1-rev-c-signature.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV03 | [JSC 20584 Revision D, Spacecraft Maximum Allowable Concentrations for Airborne Contaminants](https://www.nasa.gov/wp-content/uploads/2026/02/jsc-20584-smacs-rev-d.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV04 | [JSC 63414 Revision A, Spacecraft Water Exposure Guidelines (SWEGs)](https://www.nasa.gov/wp-content/uploads/2024/02/jsc-63414-swegs-rev-a.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV05, RT09 | [OCHMO-TB-020 Design for Ionizing Radiation Protection, Rev G (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-020-radiation-protection.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |
| EV06 | [OCHMO-TB-003 Habitable Atmosphere, Rev A (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-003-habitable-atmosphere.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV07, RS45 | [OCHMO-TB-015 Spaceflight Toxicology, Rev B (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-015-spaceflight-toxicology.pdf) | [the habitat](ENVIRONMENTS.md), [Someone needs help](RESPONDER-FLOWS.md) |
| EV08, RT13 | [OCHMO-TB-011 Lunar Dust, Rev C (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-011-lunar-dust-technical-brief.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |
| EV09, RT05 | [OCHMO-TB-035 Acoustics, Rev C (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-035-acoustics.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |
| EV10 | [OCHMO-TB-027 Water, Rev C (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-027-water.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV11, RT21 | [OCHMO-TB-046 Microbiology in Space Overview, Rev A (technical brief)](https://www.nasa.gov/wp-content/uploads/2025/05/ochmo-tb-046-microbiology-rev-a.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |
| EV12, RT10, RS51 | [OCHMO-TB-037 Decompression Sickness (DCS) Risk Mitigation, Revision D (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-037-decompression-sickness.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md), [Someone needs help](RESPONDER-FLOWS.md) |
| EV14 | [OCHMO-TB-008 Fire Protection, Rev A (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-008-fire-protection.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV15 | [Nominal Spacesuit Carbon Dioxide Levels Standard Technical Bulletin](https://www.nasa.gov/wp-content/uploads/2025/02/suited-co2-technical-bulletin-02182025.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV16 | [OCHMO-TB-030 Bone Loss, Revision D (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-030-bone-loss.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV17 | [OCHMO-TB-010 Sensorimotor, Rev A (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-010-sensorimotor.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV18 | [OCHMO-TB-026 Lighting Design, Rev C (technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-026-lighting-design.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV19 | [Risk of Adverse In-Mission Health and Performance Effects and Long-Term Health Effects Due to Celestial Dust Exposure (Dust Risk), NASA risk page](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-adverse-health-performance-effects-of-celestial-dust-exposure/) | [the habitat](ENVIRONMENTS.md) |
| EV20 | [Risk of Adverse Mission Outcomes Due to Earth Independent Human-Systems Operations (EIHSO Risk), NASA risk page](https://www.nasa.gov/directorates/esdmd/hhp/eihso-risk/) | [the habitat](ENVIRONMENTS.md) |
| EV21 | [Risk of Reduced Crew Health and Performance Due to Hypoxia, NASA risk page](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-reduced-crew-health-and-performance-due-to-hypoxia/) | [the habitat](ENVIRONMENTS.md) |
| EV22 | [Risk of Reduced Physical Performance Capabilities Due to Reduced Muscle Size, Strength, and Endurance (Muscle Risk), NASA risk page](https://www.nasa.gov/directorates/esdmd/hhp/muscle-risk/) | [the habitat](ENVIRONMENTS.md) |
| EV23, RS35 | [Evidence Report: Risk of Adverse Health and Performance Effects of Celestial Dust Exposure (Human Research Program)](https://humanresearchroadmap.nasa.gov/Evidence/reports/Dust.pdf) | [the habitat](ENVIRONMENTS.md), [Someone needs help](RESPONDER-FLOWS.md) |
| EV24 | [JSC 20584 Revision C, Spacecraft Maximum Allowable Concentrations for Airborne Contaminants](https://ntrs.nasa.gov/api/citations/20240007713/downloads/JSC%2020584_SMACs_Rev%20C_FINAL.pdf) | [the habitat](ENVIRONMENTS.md) |
| EV25 | [Hazard: Distance from Earth, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-distance-from-earth/) | [the habitat](ENVIRONMENTS.md) |
| EV26 | [Hazard: Gravity Fields, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-gravity-fields/) | [the habitat](ENVIRONMENTS.md) |
| EV27 | [Hazard: Isolation and Confinement, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-isolation-and-confinement/) | [the habitat](ENVIRONMENTS.md) |
| EV28 | [Hazard: Space Radiation, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-space-radiation/) | [the habitat](ENVIRONMENTS.md) |
| EV29 | [Hazard: Hostile/Closed Environments, NASA Human Research Program](https://www.nasa.gov/hrp/hazard-hostile-closed-environments/) | [the habitat](ENVIRONMENTS.md) |
| RT01, RS68 | [OCHMO-TB-044 Medical Operations: Spaceflight Considerations](https://www.nasa.gov/wp-content/uploads/2025/08/ochmo-tb-044-medical-operations.pdf) | [the routine](ROUTINE.md), [Someone needs help](RESPONDER-FLOWS.md) |
| RT02 | [NASA-STD-3001 Volume 1, Revision C: Crew Health](https://www.nasa.gov/wp-content/uploads/2023/11/nasa-std-3001-vol-1-rev-c-with-signature.pdf) | [the routine](ROUTINE.md) |
| RT04 | [OCHMO-MTB-004 Space Adaptation Sickness (SAS), Rev A](https://www.nasa.gov/wp-content/uploads/2025/09/ochmo-mtb-004-space-adaptation-sickness-sas.pdf) | [the routine](ROUTINE.md) |
| RT06 | [Effects of Long Duration Space Flight on Low Frequency Hearing (Coste and Robinette, NTRS 20240009887)](https://ntrs.nasa.gov/citations/20240009887) | [the routine](ROUTINE.md) |
| RT07 | [Status: International Space Station (ISS) Crewmembers' Noise Exposures (Inter-Noise 2021, NTRS 20210016305)](https://ntrs.nasa.gov/citations/20210016305) | [the routine](ROUTINE.md) |
| RT08 | [Status: Crewmember Noise Exposures on the International Space Station (ICES 2015, NTRS 20150011048)](https://ntrs.nasa.gov/citations/20150011048) | [the routine](ROUTINE.md) |
| RT11 | [Evidence Report: Risk of Reduced Crew Health and Performance Due to Decompression Sickness and Hypoxia](https://humanresearchroadmap.nasa.gov/Evidence/reports/Evidence%20Report%20Hypoxia%20and%20DCS_FINAL_1-8-2024.pdf) | [the routine](ROUTINE.md) |
| RT12 | [Evidence Report: Risk of Injury and Compromised Performance Due to EVA Operations](https://humanresearchroadmap.nasa.gov/Evidence/other/EVA%20Injury%20Evidence%20Report%20FINAL_6-14-2023.pdf) | [the routine](ROUTINE.md) |
| RT14 | [OCHMO-TB-031 Exercise Overview](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-031-exercise-overview.pdf) | [the routine](ROUTINE.md) |
| RT15 | [Behavioral Health and Performance Operations at the NASA Johnson Space Center (Beven, AsMA 2017, NTRS 20170003866)](https://ntrs.nasa.gov/citations/20170003866) | [the routine](ROUTINE.md) |
| RT16 | [Health and Fitness Evaluations for Long Duration Microgravity Exposure (Roden and Ewert, abstract, NTRS 20060051792)](https://ntrs.nasa.gov/citations/20060051792) | [the routine](ROUTINE.md) |
| RT17 | [Crew Health and Performance Integrated Data Service Platform (CHP-IDSP): Project Updates (HRP IWS 2025, NTRS 20250000330)](https://ntrs.nasa.gov/citations/20250000330) | [the routine](ROUTINE.md) |
| RT18 | [Crew Time Analysis of Planned Medical Activities & Estimate of Time Required for 6 Month Increment (Dulin, Easter, Stratton, NTRS 20230015627)](https://ntrs.nasa.gov/citations/20230015627) | [the routine](ROUTINE.md) |
| RT19 | [International Space Station Medical Operations (Jones, NTRS 20080010877)](https://ntrs.nasa.gov/citations/20080010877) | [the routine](ROUTINE.md) |
| RT20 | [Microbial Detection and Control on the International Space Station (Castro, November 2014, NTRS 20150001643)](https://ntrs.nasa.gov/citations/20150001643) | [the routine](ROUTINE.md) |
| RT22 | [OCHMO-MTB-012 Pharmaceutical Care, Rev B](https://www.nasa.gov/wp-content/uploads/2026/01/ochmo-mtb-012-pharmaceutical-care.pdf) | [the routine](ROUTINE.md) |
| RT23 | [OCHMO-TB-051 Crew Medical Support for Entry, Descent, and Landing](https://www.nasa.gov/wp-content/uploads/2026/05/ochmo-tb-051-crew-medical-support-entry-descent-landing.pdf) | [the routine](ROUTINE.md) |
| RT24 | [OCHMO-TB-006 Health Stabilization Program, Rev F](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-006-health-stabilization-program.pdf) | [the routine](ROUTINE.md) |
| RT33 | [OCHMO-MTB-006 Shoulder Injury Overview & Treatment Guidance](https://www.nasa.gov/wp-content/uploads/2025/09/ochmo-mtb-006-shoulder-injury.pdf) | [the routine](ROUTINE.md) |
| RS07 | [ICL106 Sudden Cardiac Arrest Clinical Finding Form (CliFF), 2023 (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/sudden-cardiac-arrest-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS10 | [Choking/Obstructed Airway CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/choking-obstructed-airway-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS14 | [Traumatic Hypovolemic Shock CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/traumatic-hypovolemic-shock-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS24 | [Seizures CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/seizures-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS28 | [Minor Head Injury (Trauma) CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-minor-head.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS29 | [Severe Head Injury (Trauma) CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-severe-head-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS32 | [Eye Foreign Body CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/eye-foreign-body-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS33 | [Burn Chemical Eye CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/burn-chemical-eye-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS34 | [Lunar Dust Exposure (EVA/Habitat) CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/dust-exposure-lunar-eva-habitat-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS40 | [Burn Moderate-Severe Thermal Skin CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/burn-moderate-to-severe-thermal-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS41 | [Evolution of Hardware and Philosophy of Emergency Response Actions on the International Space Station and Future Spacecrafts. Tobias B, McMillan C (NASA JSC), SpaceOps 2023](https://ntrs.nasa.gov/api/citations/20220019247/downloads/Emergency%20Ops%20History%20for%20SpaceOps%20Conference%202023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS42 | [Toxic Exposure (Ammonia) CliFF (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-exposure-ammonia-cliff.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS43 | [Toxic Inhalation Exposure (Combustion Products) CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-inhalation-exposure-combustion-products-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS44 | [Smoke Inhalation CliFF (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/smoke-inhalation-cliff.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS49 | [Evidence Report: Risk of Reduced Crew Health and Performance due to Decompression Sickness and Hypoxia (NASA Human Research Program)](https://humanresearchroadmap.nasa.gov/Evidence/other/Evidence%20Report%20Hypoxia%20and%20DCS_FINAL_1-8-2024.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS52 | [EVA-Related Decompression Sickness CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-decompression-sickness-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS53 | [How NASA Will Protect Astronauts From Space Radiation at the Moon (NASA Science)](https://science.nasa.gov/science-research/heliophysics/how-nasa-will-protect-astronauts-from-space-radiation-at-the-moon) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS54 | [To Protect Artemis II Astronauts, NASA Experts Keep Eyes on Sun (NASA Science)](https://science.nasa.gov/missions/artemis/artemis-2/to-protect-artemis-ii-astronauts-nasa-experts-keep-eyes-on-sun/) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS55 | [Acute Radiation Syndrome CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/acute-radiation-syndrome-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS58 | [Anaphylaxis CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/anaphylaxis-cliff-2023.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| RS67 | [Behavioral Emergency CliFF (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/behavioral-emergency-cliff.pdf) | [Someone needs help](RESPONDER-FLOWS.md) |
| C09 | [OCHMO-TB-019 Orthostatic Intolerance, Rev B](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-019-orthostatic-intolerance.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |
| C14 | [OCHMO-TB-016 Behavioral Health and Performance, Rev B](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-016-behavioral-health.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |
| C15 | [OCHMO-TB-041 Sleep Accommodations, Rev B](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-041-sleep-accommodations.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |
| C23 | [OCHMO-MTB-009 Circadian Desynchrony](https://www.nasa.gov/wp-content/uploads/2026/03/ochmo-mtb-009-circadian-desynchrony.pdf) | [the habitat](ENVIRONMENTS.md) |
| C25 | [OCHMO-TB-004 Carbon Dioxide, Revision D](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-004-carbon-dioxide.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |
| N04 | [5 Hazards of Human Spaceflight, NASA Human Research Program](https://www.nasa.gov/hrp/hazards/) | [the habitat](ENVIRONMENTS.md) |
| S12 | [OCHMO-MTB-001 Spaceflight Associated Neuro-ocular Syndrome (SANS)](https://www.nasa.gov/wp-content/uploads/2024/02/ochmo-mtb-001-sans.pdf) | [the habitat](ENVIRONMENTS.md), [the routine](ROUTINE.md) |

### NASA data sources and known-outcome cases

The sources of the files in [nasa-data/](nasa-data/README.md) and of its known-outcome cases (AGENTS.md section 4, "Replay known outcomes"). Each data file's provenance JSON names its ID; [nasa-data/README.md](nasa-data/README.md) lists every file with its span, cadence and consumers. The OSDR RadLab API behind the radiation files and the Mars case is N20, catalogued with the habitat simulator. N40 to N42 are journal papers with NASA authors, not NASA publications; they are used only for the case details NASA's own documents do not print.

| ID | Document | What it is | What it does in the product |
|---|---|---|---|
| N30 | [CDAWeb ISS_DOSANL_TEPC](https://cdaweb.gsfc.nasa.gov/misc/NotesI.html#ISS_DOSANL_TEPC), [HAPI info](https://cdaweb.gsfc.nasa.gov/hapi/info?id=ISS_DOSANL_TEPC), DOI [10.48322/0ba1-1764](https://doi.org/10.48322/0ba1-1764) | NASA Space Radiation Analysis Group TEPC aboard the ISS: dose (µGy per minute) and dose equivalent (µSv per minute) about every minute, UTC, 2010 to 2018 | Second radiation instrument for the September 2017 solar particle event: the simulator's dose and dose equivalent channels, the mission dose tally and a daily dose series (nasa-data/radiation/) |
| N31 | [Toxicological Assessment of ISS Air and Water Quality, Increment 71, TOX-SW-2025-02](https://www.nasa.gov/wp-content/uploads/2025/12/increment-71-report-including-ng-21-ingress-final.pdf), Tables 1, 2, 3A, S-1, S-2A and S-3A | JSC Toxicology and Environmental Chemistry report for March to September 2024: grab-sample concentrations and T-values, ANITA-2 monthly averages, potable water results with their limits | The VOC toxicity index and water quality golden cases, and real nominal CO, ammonia and VOC levels for the simulator (nasa-data/habitat-environment/, nasa-data/water-quality/) |
| N32 | [International Space Station Acoustics: A Status Report, ICES-2024-354](https://ntrs.nasa.gov/citations/20240006442), pp. 2 to 6 | NASA JSC acoustics survey results 2022 to 2024: module and sleep station sound levels, NC ratings and speech interference levels | The simulator's noise levels per area and the noise warning golden inputs (nasa-data/acoustics/) |
| N33 | [Status: ISS Crewmembers' Noise Exposures, Inter-Noise 2021 paper 2219](https://ntrs.nasa.gov/citations/20210016305), p. 4 | Crew-worn dosimetry, Increments 51 to 64: LEQ16 and LEQ8, and the crew member "required to don HPD" after 73.0 dBA | The noise exposure golden case with a documented crew action (nasa-data/acoustics/) |
| N34 | [OSDR Inspiration4 mission](https://osdr.nasa.gov/bio/repo/data/missions/SpaceX%20Inspiration4): [OSD-569](https://osdr.nasa.gov/bio/repo/data/studies/OSD-569) and [OSD-575](https://osdr.nasa.gov/bio/repo/data/studies/OSD-575) | Open complete blood count and metabolic panel of the four Inspiration4 crew members, seven draws from 92 days before launch to 194 days after return | Real per-person laboratory series for personal baselines and return to baseline; never an in-flight trend (nasa-data/crew-physiology/) |
| N35 | [Apollo 13 Mission Report, MSC-02680](https://ntrs.nasa.gov/citations/19710003598) | NASA's mission report, September 1970: lunar module CO2, cabin temperature, water and crew status after the oxygen tank incident | Case apollo13-lm-co2-cold-uti: printed values and the documented outcome |
| N36 | [Biomedical Results of Apollo, NASA SP-368](https://ntrs.nasa.gov/citations/19760005580), Section II Chapter 1 | Clinical aspects of Apollo crew health, including the Apollo 15 arrhythmia and the postflight potassium deficit, and the Apollo 13 infection | Cases apollo15-cardiac-arrhythmia-potassium and apollo13-lm-co2-cold-uti |
| N37 | [Evidence Report: Risk of Spaceflight-Induced Intracranial Hypertension and Vision Alterations, 2012](https://humanresearchroadmap.nasa.gov/evidence/reports/VIIP.pdf) | NASA HRP evidence report with seven ISS case descriptions of vision changes (now SANS) | Case iss-sans-viip-cases-2012 |
| N38 | In-Flight Carbon Dioxide Exposures and Related Symptoms, NASA/TP-2010-216126 ([copy read](https://inspectapedia.com/hazmat/In-Flight-CO2-Exposure-Implications-Law-NASA.pdf), a third-party host: NASA's own copy did not respond and NTRS has no record) | NASA technical paper by Law, Watkins and Alexander, 2010: CO2 levels with crew symptom reports on the ISS and Shuttle; OCHMO-TB-004 (C25) cites it for its symptom ranges | Case iss-co2-symptom-reports-2001-2009 |
| N39 | [Large Solar Storm Sparks Global Aurora and Doubles Radiation Levels on the Martian Surface, JPL news 2017-254](https://www.jpl.nasa.gov/news/large-solar-storm-sparks-global-aurora-and-doubles-radiation-levels-on-the-martian-surface) | NASA JPL release, 29 September 2017: Curiosity's RAD saw surface radiation more than double for more than two days | Case spe-2017-09-mars-surface: the documented outcome |
| N40 | [The Solar Particle Event on 10 September 2017 as observed onboard the International Space Station, Space Weather 2018](https://doi.org/10.1029/2018SW001920) ([NTRS record](https://ntrs.nasa.gov/citations/52387849917688), [accepted manuscript read](https://elib.dlr.de/120788/1/ME-SBA-2018-Berger-Matthi%C3%A4_et%20al-DOSIS-3D_Space%20Weather.pdf)) | Berger et al., with NASA SRAG authors: onset and dose of the event at the ISS and on Mars | Cases spe-2017-09-iss-columbus and spe-2017-09-mars-surface: onset times and documented dose |
| N41 | [Assessment of Jugular Venous Blood Flow Stasis and Thrombosis During Spaceflight, JAMA Network Open 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6902784/) | Marshall-Goebel et al., NASA-funded ISS cohort: one crew member with an occlusive internal jugular vein thrombus, found at about flight day 50 | Case iss-ijv-thrombosis-anticoagulant-supply: the timing of the finding |
| N42 | [Venous Thrombosis during Spaceflight, N Engl J Med 2020;382:89-90](https://www.nejm.org/doi/full/10.1056/NEJMc1905875) | Letter with a NASA JSC author: treatment aboard with a limited enoxaparin supply until apixaban arrived | Case iss-ijv-thrombosis-anticoagulant-supply: stock, doses and the documented response |

## 2. NASA data in this package

Downloaded from NASA, unchanged: no interpolation and no unit conversion. Every file and known-outcome case, by category, with its span, cadence, row count and consumers, is listed in [nasa-data/README.md](nasa-data/README.md), together with what was searched for and is restricted or unavailable. The two files the demo path uses, downloaded on 14 November 2026 from NASA's Open Science Data Repository, are these.

| File | What it holds | Use |
|---|---|---|
| [eda_rr1_telemetry.csv](nasa-data/habitat-environment/eda_rr1_telemetry.csv) | 12,087 rows from 2014-09-20 03:59 to 2014-11-01 03:54, mostly every five minutes: time, then ISS and ground temperature (°C), relative humidity (%) and CO2 (ppm) | The cabin air card. The demo stops the replay clock at 2014-10-07 15:36, where the twelve five-minute samples of the last hour average 4,606 ppm |
| [eda_rr1_provenance.json](nasa-data/habitat-environment/eda_rr1_provenance.json) | Source URL, retrieval time, mission metadata, units, statistics and checksums | Cite it on the project page; check the checksum before a demo |
| [radlab_dostel_sample.csv](nasa-data/radiation/radlab_dostel_sample.csv) | 180 records from the DosTel1 and DosTel2 instruments in the ISS Columbus module, 2022-04-01 23:00 to 2022-04-02 01:05: absorbed dose rate in µGy per hour | The radiation reading on the space station: the 84 DosTel1 records, mean 6.2 µGy per hour, labeled Replay as an instrument's dose rate |
| [radlab_dostel_sample.provenance.json](nasa-data/radiation/radlab_dostel_sample.provenance.json) | The query, the API documentation link, retrieval time and checksum | |

**From ppm to mmHg.** ppm is a fraction; mmHg is a partial pressure. ppCO2 in mmHg is ppm × total pressure in mmHg ÷ 1,000,000. The file has no cabin pressure, so the package assumes 760 mmHg and says so wherever it converts: 3 mmHg is then 3,947 ppm, and 4,606 ppm is 3.50 mmHg.

**What the files cannot say.** The portal labels the channels "ISS" and "Ground" but does not name the sensor's location or calibration, and it notes that ground dates were matched to flight dates. The RR-1 trace is another mission's cabin in 2014, never a crew member's exposure. The radiation sample is an instrument's dose rate, never a person's dose, and comes from a different year.

## 3. NASA work close to ours

| Work | What NASA describes | What it means for us |
|---|---|---|
| [Crew Health and Performance Integrated Data Architecture (CHP-IDA)](https://ntrs.nasa.gov/citations/20240004807) | A back end for environmental, exercise, medical, sleep and performance data, and the problem of siloed, unsynchronized data | Supports our motivation and data categories. It is not an integration with NASA |
| [CHP-IDSP project updates, 2025](https://ntrs.nasa.gov/api/citations/20250000330/downloads/CHP-IDSP%20Project%20Updates_IWS%202025.pdf) | Periodic health exams, CO2 notification and sleep scenarios, device data and synchronization | A workflow precedent. Do not claim NASA has no daily-health or CO2 scenario |
| [Crew Medical Officer Digital Assistant (CMO-DA)](https://ntrs.nasa.gov/api/citations/20250004306/downloads/DocInABox_AsMA%202025_Final.pdf) | "a multi-modal, agentic artificial intelligence system to support Crew Medical Officer decision-making in autonomous environments enabling Earth Independent Medical Operations", with "77-88% accuracy vs. physician" on structured clinical exams, and work on running it at the edge; local inference reported in ground testing in 2026 | The closest precedent for the assistant and the direction NASA is taking; the pitch cites it first. Ground testing, not flight validation; no public weights or clinical knowledge package. We do not compete on diagnostic accuracy: the product is the layer around any such assistant (what this kit can test and do, the plans packed before launch, and a crewmate's hands checked by the cameras), and every plan runs with the assistant switched off |
| [INDUS](https://science.nasa.gov/open-science/ai-language-model-science-research/) | Scientific encoders and retrieval models released by NASA and IBM | Candidate models for the retrieval layer. Not a diagnostic model |
| [PVT+](https://www.nasa.gov/human-systems-integration-division/human-performance/fatigue-countermeasures-laboratory/) | A research app combining a reaction test, sleep diaries and questionnaires | The design precedent for our reaction test. The research found no open code for it |

## 4. Rules for using NASA material

- **Membership is not capability.** A condition on NASA's list is modeled for exploration missions. It does not mean this kit can test for it.
- **Briefs are for reference.** The technical briefs say they do not give medical recommendations for individuals (S12). They explain spaceflight context on the cards; they never instruct a crew member.
- **A replay is not an exposure.** Historical cabin data illustrates the environment and is always labeled Replay with its dates.
- **Units and revisions are kept.** Keep the original units, state every conversion and its assumption, and cite the revision letter. One brief (C25) cites the CO2 requirement under earlier revision letters of Volume 2; Rev F (N01) is the current one.
- **Names are citations, never branding.** NASA documents and data are named in plain text. No NASA logo, flag, insignia or mission identifier appears in the product, the video or the slides.
- **The ground is a reader.** Sources about delay and line of sight (N07 to N11, EV25) set the ground link's schedule. None of them makes the ground a step that a plan, flow, warning or routine task waits for.
- **Cite the requirement, not a level.** NASA-STD-3001 Volume 1 replaced its Levels of Care with technical requirements. Cite [V1 3004], [V1 3006] and [V1 3007] (EV02); the levels of Revision A (N12) are history.
- **Cite each source for what it says.** OCHMO-TB-044 (RS68, RT01) is cited for medical operations duties and "two CMOs for each increment", never for delay, Earth independence or return times. The EIHSO risk page (EV20) is about operating the vehicle and habitat without ground support, not about medicine. Mars conjunction comes about every 26 months and cuts or degrades contact for one to several weeks (N10, N11).


## 5. The full catalog

Every NASA document, and the few related papers, that the research found: 580 unique links, grouped by kind. Titles are shortened for reading. Being listed here means the research found the document, not that it was read or that it supports a claim; the documents read for a claim are in sections 1 and 2.

| Kind | Documents |
|---|---|
| [Clinical Findings Forms (CliFFs)](#cliffs) | 356 |
| [Human Research Roadmap: medical condition summaries](#hrr-conditions) | 80 |
| [Human Research Roadmap: evidence reports and research plans](#hrr-evidence) | 41 |
| [Human health and performance risks](#risks) | 30 |
| [OCHMO technical and medical briefs](#briefs) | 37 |
| [Standards and medical checklists](#standards) | 7 |
| [Condition lists and mission medical risk models](#models) | 13 |
| [Illness observed in flight and long-term health](#observed) | 16 |

<a id="cliffs"></a>

### Clinical Findings Forms (CliFFs)

Condition-level forms from NASA's Integrated Medical Model documentation: how each modeled condition presents, how it is treated in flight and what resources it needs, with their peer reviews and dated versions. The best NASA source for writing a condition's intake and red flags.

- [10.5 day Orion Optimization](https://www.nasa.gov/wp-content/uploads/2026/08/10-5-day-orion-optimization.pdf)
- [2024 Lunar](https://www.nasa.gov/wp-content/uploads/2026/08/2024-lunar.pdf)
- [Abdominal Injury (Blunt) CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/blunt-abdominal-injury-blunt-cliff-peer-review.pdf)
- [Abdominal Injury (Blunt/Trauma) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-abdominal-injury-blunt-cliff-2023.pdf)
- [Abdominal Injury CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/abdominal-injury-cliff.pdf)
- [Abdominal Injury CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/abdominal-wall-hernia-cliff-2023.pdf)
- [Abdominal Wall Hernia CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/abdominal-wall-hernia-cliff.pdf)
- [Abdominal Wall Hernia CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/abdominal-wall-hernia-cliff-peer-review.pdf)
- [Abnormal Uterine Bleeding CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/abnormal-uterine-bleeding-cliff.pdf)
- [Abnormal Uterine Bleeding CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/abnormal-uterine-bleeding-cliff-2023.pdf)
- [Abnormal Uterine Bleeding CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/acute-uterine-bleeding-cliff-peer-review.pdf)
- [Acute Angle-Closure Glaucoma CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/acute-angle-closure-glaucoma-cliff.pdf)
- [Acute Arthritis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/acute-arthritis-cliff.pdf)
- [Acute Arthritis CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/arthritis-acute-cliff-2023.pdf)
- [Acute Cholesystitis Biliary Colic CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/acute-cholecystitis-biliary-colic-cliff.pdf)
- [Acute Cholesystitis Biliary Colic CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/cholelithiasis-biliary-colic-acute-cliff-2023.pdf)
- [Acute Compartment Syndrome CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/acute-compartment-syndrome-cliff.pdf)
- [Acute Coronary Syndrome CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/acute-coronary-syndrome-cliff-2023.pdf)
- [Acute Coronary Syndrome CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/acute-coronary-syndrome-cliff-peer-review.pdf)
- [Acute Diverticulitis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/acute-diverticulitis-cliff.pdf)
- [Acute Diverticulitis CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/diverticulitis-acute-cliff-2023.pdf)
- [Acute Pancreatitis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/acute-pancreatitis-cliff.pdf)
- [Acute Pancreatitis CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/pancreatitis-acute-cliff-2023.pdf)
- [Acute Pancreatitis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/acute-pancreatitis-cliff-peer-review.pdf)
- [Acute Prostatitis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/acute-prostatitis-cliff.pdf)
- [Acute Prostatitis ClIFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/prostatitis-acute-cliff-2023.pdf)
- [Acute Radiation Syndrome CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/acute-radiation-syndrome-cliff.pdf)
- [Acute Radiation Syndrome CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/acute-radiation-syndrome-cliff-2023.pdf)
- [Acute Radiation Syndrome CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/acute-radiation-syndrome-cliff-peer-review.pdf)
- [Acute Sinusitis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/acute-sinusitis-cliff.pdf)
- [Additional Mars DRMs](https://www.nasa.gov/wp-content/uploads/2026/08/additional-mars-drms.pdf)
- [Adjustment Disorder CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-adjustment-disorder-cliff-2023.pdf)
- [Allergic Reaction (Mild-Moderate) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/allergic-reaction-mild-to-moderate-cliff.pdf)
- [Allergic Reaction (Mild-Moderate) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/allergic-reaction-mild-to-moderate-cliff-2023.pdf)
- [Allergic Reaction (Mild-Moderate) CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/allergic-reaction-mild-to-moderate-cliff-peer-review.pdf)
- [Altitude Sickness CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/altitude-sickness-cliff.pdf)
- [Altitude Sickness CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/altitude-sickness-cliff-2023.pdf)
- [Altitude Sickness CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/altitude-sickness-cliff-peer-review.pdf)
- [Anaphylaxis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/anaphylaxis-cliff.pdf)
- [Anaphylaxis CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/anaphylaxis-cliff-2023.pdf)
- [Anaphylaxis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/anaphylaxis-cliff-peer-review.pdf)
- [Angina/Myocardial Infarction CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/angina-myocardial-infarction-cliff.pdf)
- [Ankle Sprain CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/ankle-sprain-strain-cliff.pdf)
- [Anxiety CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/anxiety-cliff.pdf)
- [Anxiety CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-anxiety-cliff-2023.pdf)
- [Appendicitis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/appendicitis-cliff.pdf)
- [Appendicitis CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/appendicitis-cliff-2023.pdf)
- [Appendicitis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/appendicitis-cliff-peer-review.pdf)
- [Arm Fracture CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/fracture-arm-cliff-2023.pdf)
- [Arm Fracture CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/arm-fracture-cliff-peer-review.pdf)
- [Arthritis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/arthritis-cliff-peer-review.pdf)
- [Atrial Fibrillation/Atrial Flutter CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/atrial-fibrillation-atrial-flutter-cliff.pdf)
- [Atrial Fibrillation/Atrial Flutter CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/atrial-fibrillation-atrial-flutter-cliff-2023.pdf)
- [Back Pain (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-back-pain-cliff-2023.pdf)
- [Back Pain (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/back-pain-space-adaptation-cliff.pdf)
- [Back Sprain/Strain CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/back-sprain-strain-cliff.pdf)
- [Back Sprain/Strain CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/sprain-strain-back-cliff-2023.pdf)
- [Back Sprain/Strain CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/back-sprain-strain-cliff-peer-review.pdf)
- [Bacterial Skin Infections CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/bacterial-skin-infections-cliff-peer-review.pdf)
- [Barotrauma (Ear Sinus Block) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/barotrauma-ear-sinus-block-cliff.pdf)
- [Barotrauma (Ear Sinus Block) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/barotrauma-ear-sinus-block-cliff-2023.pdf)
- [Barotrauma CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/barotrauma-cliff-peer-review.pdf)
- [Behavioral Emergency CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/behavioral-emergency-cliff.pdf)
- [Benzodiazapine/Opioid Overdose CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/benzodiazapine-or-opioid-overdose-cliff-2023.pdf)
- [Benzodiazapine/Opioid Overdose CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/benzodiazepine-and-opiate-overdose-cliff-peer-review.pdf)
- [BHP Adjustment Disorder CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-adjustment-disorder-cliff-peer-review.pdf)
- [BHP Anxiety CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-anxiety-cliff-peer-review.pdf)
- [BHP Depression CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-depression-cliff-peer-review.pdf)
- [BHP Grief Reaction CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-grief-reaction-cliff-peer-review.pdf)
- [BHP Psychosis (Secondary to Depression) CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-psychosis-secondary-to-depression-cliff-peer-review.pdf)
- [BHP Sleep Disturbance CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-sleep-disturbance-cliff-peer-review.pdf)
- [Burn Chemical Eye CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/burn-chemical-eye-cliff-2023.pdf)
- [Burn Chemical Eye CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/burn-chemical-eye-cliff-peer-review.pdf)
- [Burn Chemical Skin CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/burn-chemical-skin-cliff-2023.pdf)
- [Burn Chemical Skin CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/burn-chemical-skin-cliff-peer-review.pdf)
- [Burn Mild CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/burn-mild-cliff-peer-review.pdf)
- [Burn Mild Thermal Skin CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/burn-mild-thermal-cliff-2023.pdf)
- [Burn Moderate-Severe Thermal Skin CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/burn-moderate-to-severe-thermal-cliff-2023.pdf)
- [Burn Moderate-Severe Thermal Skin CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/burn-moderate-to-severe-thermal-cliff-peer-review.pdf)
- [Burns (Secondary to Fire) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/burns-secondary-to-fire-cliff.pdf)
- [Cardiogenic Shock CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/shock-cardiogenic-cliff-2023.pdf)
- [Cardiogenic Shock/Myocardial Infarction CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/cardiogenic-shock-secondary-to-myocardial-infarction-cliff.pdf)
- [Central Neuropathy (Impingement-Related) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/neuropathy-central-impingement-related-cliff-2023.pdf)
- [Cerebrovascular Accident CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/cerebrovascular-accident-cliff-2023.pdf)
- [Cerebrovascular Accident CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/cerebrovascular-accident-cliff-peer-review.pdf)
- [Cerumen Impaction CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/cerumen-impaction-cliff-2023.pdf)
- [Cerumen Impaction CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/cerumen-impaction-cliff-peer-review.pdf)
- [Cervical Spine Fracture CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/fracture-cervical-spine-cliff-2023.pdf)
- [Cervical Spine Fracture CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/cervical-spine-fracture-cliff-peer-review.pdf)
- [Chest Injury (Blunt/Trauma) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-chest-injury-blunt-cliff-2023.pdf)
- [Chest Injury CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/chest-injury-cliff.pdf)
- [Choking/Obstructed Airway CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/choking-obstructed-airway-cliff-2023.pdf)
- [Choking/Obstructed Airway CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/choking-obstructed-airway-cliff-peer-review.pdf)
- [Choking/Obstructied Airway CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/choking-obstructed-airway-cliff.pdf)
- [Cholelithiasis to Biliary Colic CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/cholelithiasis-biliary-colic-acute-peer-review.pdf)
- [Cis Lunar Orbiter Docked PRA](https://www.nasa.gov/wp-content/uploads/2026/08/cis-lunar-oribter-docked-pra.pdf)
- [Cis Martian Orbiter 4 Crew Report](https://www.nasa.gov/wp-content/uploads/2026/08/cis-martian-orbiter-4-crew-report.pdf)
- [Cold Injury-Chilblains/Frostbite CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/cold-injury-chilblains-frostbite-cliff-peer-review-final.pdf)
- [Constipation (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/constipation-space-adaptation-cliff.pdf)
- [Constipation (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-constipation-cliff-2023.pdf)
- [Decompression Sickness (Secondary to EVA) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/decompression-sickness-secondary-to-extravehicular-activity-cliff.pdf)
- [Deep Space Habitat](https://www.nasa.gov/wp-content/uploads/2026/08/deep-space-habitat.pdf)
- [Dental Abscess CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/dental-abscess-cliff.pdf)
- [Dental Abscess CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/dental-abscess-cliff-2023.pdf)
- [Dental Abscess CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/dental-abscess-cliff-peer-review.pdf)
- [Dental Avulsion (Tooth Loss) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/dental-avulsion-tooth-loss-cliff.pdf)
- [Dental Caries CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/dental-caries-cliff.pdf)
- [Dental Cross Loss CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/dental-crown-loss-cliff.pdf)
- [Dental Cross Loss CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/dental-crown-loss-cliff-2023.pdf)
- [Dental Exposed Pulp CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/dental-exposed-pulp-cliff.pdf)
- [Dental Exposed Pulp CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/dental-fracture-exposed-pulp-cliff-2023.pdf)
- [Dental Filling Loss CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/dental-filling-loss-cliff-2023.pdf)
- [Dental Fracture/Exposed Pulp CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/dental-fracture-exposed-pulp-cliff-peer-review.pdf)
- [Dental Luxation Avulsion CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/dental-luxation-avulsion-cliff-peer-review.pdf)
- [Dental Luxation/Avulsion (Tooth Loss) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/dental-luxation-avulsion-tooth-loss-cliff-2023.pdf)
- [Depression CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/depression-cliff.pdf)
- [Depression CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-depression-cliff-2023.pdf)
- [Detailed Lunar Risk](https://www.nasa.gov/wp-content/uploads/2026/08/detailed-lunar-risk.pdf)
- [Diarrhea CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/diarrhea-cliff.pdf)
- [Distal Leg Fracture CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/distal-leg-fracture-cliff-peer-review.pdf)
- [Diverticulitis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/diverticulitis-cliff-peer-review.pdf)
- [Ebullism CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/ebullism-cliff-2023.pdf)
- [Ebullism CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/ebulism-cliff-peer-review.pdf)
- [Elbow Dislocation CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/elbow-dislocation-cliff.pdf)
- [Elbow Sprain CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/elbow-sprain-strain-cliff.pdf)
- [Epistaxis (Space Adaptation) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-epistaxis-cliff-2023.pdf)
- [Epistaxis CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/epistaxis-cliff-2023.pdf)
- [Epistaxis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/epistaxis-cliff-peer-review.pdf)
- [EVA-Related Decompression Sickness CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-decompression-sickness-cliff-2023.pdf)
- [EVA-Related Decompression Sickness CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-decompression-sickness-cliff-peer-review.pdf)
- [EVA-Related Dehydration CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-dehydration-cliff-2023.pdf)
- [EVA-Related Dehydration CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-dehydration-cliff.pdf)
- [EVA-Related Fingernail Delamination CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-fingernail-delamination-cliff-2023.pdf)
- [EVA-Related Fingernail Delamination CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-fingernail-delamination-cliff-peer-review.pdf)
- [EVA-Related Hand Injury CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-hand-injury-cliff-2023.pdf)
- [EVA-Related Heat Illness CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-heat-illness-cliff-2023.pdf)
- [EVA-Related Heat Illness CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-heat-illness-cliff-peer-review.pdf)
- [EVA-Related Paesthesia CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-paresthesia-cliff-2023.pdf)
- [EVA-Related Paesthesia CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-paresthesia-cliff-peer-review.pdf)
- [EVA-Related Shoulder Injury CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-shoulder-injury-cliff-2023.pdf)
- [EVA-Related Shoulder Injury CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-shoulder-injury-cliff-peer-review.pdf)
- [EVA-Related Suit Contact Injury CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-suit-contact-injury-cliff-2023.pdf)
- [EVA-Related Suit Contact Injury CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-suit-contact-injury-cliff-peer-review.pdf)
- [Eye Chemcial Burn CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/eye-chemical-burn-cliff.pdf)
- [Eye Corneal Ulcer CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/eye-corneal-ulcer-cliff.pdf)
- [Eye Foreign Body CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eye-foreign-body-cliff-2023.pdf)
- [Eye Foreign Body CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eye-foreign-body-cliff-peer-review.pdf)
- [Eye Infection CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/eye-infection-cliff.pdf)
- [Eye Irritation Corneal Abrasion and Ulceration CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eye-irritation-corneal-abrasion-and-ulceration-cliff-peer-review.pdf)
- [Eye Irritation Corneal Abrasion/Ulceration CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eye-irritation-corneal-abrasion-ulceration-cliff-2023.pdf)
- [Eye Irritation/Abrasion CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/eye-irritation-abrasion-cliff.pdf)
- [Eye Penetration (Foreign Body) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/eye-penetration-foreign-body-cliff.pdf)
- [Eye Retinal Injury CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eye-retinal-injury-cliff-2023.pdf)
- [Eye Retinal Injury CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eye-retinal-injury-cliff-peer-review.pdf)
- [Eyelid and Anterior Eye Infection CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/eyelid-and-anterior-eye-infection-cliff-2023.pdf)
- [Eyelid and Anterior Eye Infection CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/eyelid-and-anterior-eye-infection-cliff-peer-review.pdf)
- [Femur Fracture CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/femur-fracture-cliff-peer-review.pdf)
- [Finger Dislocation CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/finger-dislocation-cliff.pdf)
- [Finger Dislocation CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/dislocation-finger-cliff-2023.pdf)
- [Finger Dislocation CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/finger-dislocation-cliff-peer-review.pdf)
- [Fingernail Delamination (Secondary to EVA) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/fingernail-delamination-secondary-to-extravehicular-activity-cliff.pdf)
- [Fracture Distal Leg CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/fracture-distal-leg-cliff-2023.pdf)
- [Fracture Femur ClIFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/fracture-femur-cliff-2023.pdf)
- [Fracture Hand CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/fracture-hand-cliff-2023.pdf)
- [Fracture Thoracic Lumbar Spine (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/fracture-thoracic-lumbar-spine-cliff-2023.pdf)
- [Fracture Wrist CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/fracture-wrist-cliff-2023.pdf)
- [Gastritis/Reflux CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/gastritis-reflux-cliff-peer-review.pdf)
- [Gastritis/Reflux/Esophagitis CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/gastritis-reflux-esophagitis-cliff-2023.pdf)
- [Gastroenterisis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/gastroenteritis-cliff-peer-review.pdf)
- [Gastroenteritis Acute Diarrhea (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/gastroenteritis-acute-diarrhea-cliff-2023.pdf)
- [Gastroenteritis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/gastroenteritis-cliff.pdf)
- [Glaucoma (Acute Angle Closure) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/glaucoma-acute-angle-closure-cliff-2023.pdf)
- [Gravity Well Entry (Motion Sickness) Cliff (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/gravity-well-entry-motion-sickness-cliff-2023.pdf)
- [Gravity Well Entry (Orthostatic Intolerance) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/gravity-well-orthostatic-intolerance-cliff-2023.pdf)
- [Gravity Well Entry (Sensorimotor Neurovestibular Disturbance) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/gravity-well-entry-sensorimotor-neurovestibular-disturbance-cliff-2023.pdf)
- [Gravity Well Entry Motion Sickness Cliff Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/gravity-well-entry-motion-sickness-cliff-peer-review.pdf)
- [Gravity Well Entry Neurovestibular Disturbance Cliff Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/gravity-well-neurovestibular-disturbance-cliff-peer-review.pdf)
- [Gravity Well Orthostatic Intolerance CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/gravity-well-orthostatic-intolerance-cliff-peer-review.pdf)
- [Grief Reaction CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-grief-reaction-cliff-2023.pdf)
- [Hand Fracture CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/hand-fracture-cliff-peer-review.pdf)
- [Head Injury CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/head-injury-cliff.pdf)
- [Headache (CO2 Induced) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/headache-co2-induced-cliff.pdf)
- [Headache (CO2-Induced) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/headache-co2-induced-cliff-2023.pdf)
- [Headache (Late) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/headache-late-cliff.pdf)
- [Headache (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/headache-space-adaptation-cliff.pdf)
- [Headache (Space Adaptation) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-headache-cliff-2023.pdf)
- [Headache CLiFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/headache-cliff-2023.pdf)
- [Headache CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/headache-cliff-peer-review.pdf)
- [Headache CO2 Induced CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/headache-co2-induced-cliff-peer-review.pdf)
- [Hearing Loss (Noise Related) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/hearing-loss-noise-related.pdf)
- [Hearing Loss CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/hearing-loss-cliff.pdf)
- [Hearing Loss CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/hearing-loss-cliff-2023.pdf)
- [Hearing Loss CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/hearing-loss-noise-related-cliff-peer-review.pdf)
- [Hemorrhoids CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/hemorrhoids-cliff.pdf)
- [Hemorrhoids CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/hemorrhoids-cliff-2023.pdf)
- [Hemorrhoids CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/hemorrhoids-cliff-peer-review.pdf)
- [Herpes Zoster Reactivation (Shingles) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/herpes-zoster-reactivation-shingles-cliff.pdf)
- [Herpes Zoster Reactivation (Shingles) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/herpes-zoster-reactivation-shingles-cliff-2023.pdf)
- [Herpes Zoster Reactivation (Shingles) CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/hzv-reactivation-cliff-peer-review.pdf)
- [Hip Sprain/Strain CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/hip-sprain-strain-cliff.pdf)
- [Hip/Proximal Femur Fracture CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/hip-proximal-femur-fracture-cliff.pdf)
- [Hypertension CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/hypertension-cliff.pdf)
- [Hypothermia CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/hypothermia-cliff-peer-review.pdf)
- [Impact of Crew Sex Differences on Medical Outcomes for Deep Space Missions](https://www.nasa.gov/wp-content/uploads/2026/08/impact-of-crew-sex-differences-on-medical-outcomes.pdf)
- [Impact of Heroic Medical Care Measures](https://www.nasa.gov/wp-content/uploads/2026/08/impact-of-heroic-medical-care-measures.pdf)
- [Indigestion CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/indigestion-cliff.pdf)
- [Influenza CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/influenza-cliff.pdf)
- [Insomnia (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/insomnia-space-adaptation-cliff.pdf)
- [Insomnia (Space Adaptation) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-insomnia-cliff-2023.pdf)
- [ISS IMM PRA 2021 Update](https://www.nasa.gov/wp-content/uploads/2026/08/iss-imm-pra-2021-update.pdf)
- [ISS PRA EVA Model 2019](https://www.nasa.gov/wp-content/uploads/2026/08/iss-imm-pra-eva-model-2019.pdf)
- [ISS to 180 days IMM (6-month PRA Update)](https://www.nasa.gov/wp-content/uploads/2026/08/iss-%E2%80%93-180-days-6-month-pra-update.pdf)
- [Knee Sprain/Strain CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/knee-sprain-strain-cliff.pdf)
- [Lower Extremity Sprain/Strain CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/sprain-strain-lower-extremity-cliff-2023.pdf)
- [Lower Extremity Sprain/Strain CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/lower-extremity-sprain-strain-cliff-peer-review.pdf)
- [Lower Extremity Stress Fracture CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/lower-extremity-le-stress-fracture-cliff.pdf)
- [Lower Extremity Tendinopathy Enthesopathy (Bursitis/Over Use Injuries) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/tendinopathy-enthesopathy-bursitis-over-use-injuries-lower-extremity-cliff-2023.pdf)
- [Lower Extremity Tendinopathy Enthesopathy (Bursitis/Over Use Injuries) CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/tendinopathy-enthesopathy-bursitis-over-use-injury-lower-extremity-cliff-peer-review.pdf)
- [Lower Respiratory Tract Infection CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/respiratory-tract-infection-lower-cliff-2023.pdf)
- [Lower Respiratory Tract Infection CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/lower-respiratory-tract-infection-cliff-peer-review.pdf)
- [Lumbar Spine Fracture CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/lumbar-spine-fracture-cliff.pdf)
- [Lunar Dust Exposure (EVA/Habitat) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/dust-exposure-lunar-eva-habitat-cliff-2023.pdf)
- [Lunar Dust Exposure (Surface EVA) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/dust-exposure-lunar-surface-eva-cliff-2023.pdf)
- [Lunar Dust Exposure CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/lunar-dust-exposure-cliff-peer-review.pdf)
- [Mars Propulsion Transit Times](https://www.nasa.gov/wp-content/uploads/2026/08/mars-propulsion-transit-times.pdf)
- [Mars Transit IVA](https://www.nasa.gov/wp-content/uploads/2026/08/mars-transit-iva.pdf)
- [Mars Transit Times](https://www.nasa.gov/wp-content/uploads/2026/08/mars-transit-times.pdf)
- [Medical Risk Assessment for an Accelerated and Standard Mars Mission](https://www.nasa.gov/wp-content/uploads/2026/08/medical-risk-assessment-for-an-accelerated-and-standard-mars-mission.pdf)
- [Medical Risk based on Mission Duration](https://www.nasa.gov/wp-content/uploads/2026/08/medical-risk-based-on-mission-duration.pdf)
- [Medical Risk for Cis Lunar Orbiter](https://www.nasa.gov/wp-content/uploads/2026/08/medical-risk-for-cis-lunar-orbiter.pdf)
- [Medical Risk for Five Cis-lunar Orbiter Missions](https://www.nasa.gov/wp-content/uploads/2026/08/medical-risk-for-five-cis-lunar-missions.pdf)
- [Medication Overdose (Adverse Reaction) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/medication-overdose-adverse-reaction-cliff.pdf)
- [Minor Head Injury (Trauma) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-minor-head.pdf)
- [Mouth Ulcer CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/mouth-ulcer-cliff.pdf)
- [Mouth Ulcer CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/mouth-ulcer-cliff-2023.pdf)
- [Nasal Congestion (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/nasal-congestion-space-adaptation-cliff.pdf)
- [Nasal Congestion (Space Adaptation) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-nasal-congestion-cliff-2023.pdf)
- [Neck Sprain-Strain CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/neck-sprain-strain-cliff-peer-review.pdf)
- [Neck Sprain/Strain CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/neck-sprain-strain-cliff.pdf)
- [Neck Sprain/Strain CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/sprain-strain-neck-cliff-2023.pdf)
- [Nephrolithiasis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/nephrolithiasis-cliff.pdf)
- [Nephrolithiasis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/nephrolithiasis-cliff-2023.pdf)
- [Nephrolithiasis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/nephrolithiasis-cliff-peer-review.pdf)
- [NESC Crew Size Tradespace Analysis](https://www.nasa.gov/wp-content/uploads/2026/08/nesc-crew-size-tradespace-analysis.pdf)
- [Neurogenic Shock CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/neurogenic-shock-cliff.pdf)
- [Neuropathy Central Impingement-Related CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/neuropathy-central-impingement-related-cliff-peer-review.pdf)
- [Nose Bleed Space Adaptation CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/nose-bleed-space-adaptation-cliff.pdf)
- [Orion Medical Kit Contents](https://www.nasa.gov/wp-content/uploads/2026/08/orion-medical-kit-contents.pdf)
- [Orion Medical Kit Validation](https://www.nasa.gov/wp-content/uploads/2026/08/orion-medical-kit-validation.pdf)
- [Otitis Externa CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/otitis-externa-cliff.pdf)
- [Otitis Externa CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/otitis-externa-cliff-2023.pdf)
- [Otitis Externa CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/otitis-externa-cliff-peer-review.pdf)
- [Otitis Media CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/otitis-media-cliff.pdf)
- [Otitis Media CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/otitis-media-cliff-2023.pdf)
- [Otitis Media CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/otitis-media-cliff-peer-review.pdf)
- [Parethesias (Secondarty to EVA) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/paresthesias-secondary-to-extravehicular-activity-cliff.pdf)
- [Pharyngitis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/pharyngitis-cliff.pdf)
- [Pharyngitis CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/streptococcal-pharyngitis-cliff-2023.pdf)
- [Pregnancy CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/pregnancy-cliff-2023.pdf)
- [Pregnancy CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/pregnancy-cliff-peer-review.pdf)
- [Pregnancy Risk CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/pregnancy-risk-cliff-2023.pdf)
- [Psychosis (Secondary to Depression) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-psychosis-secondary-to-depression-cliff-2023.pdf)
- [Reactive Airway CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/reactive-airway-cliff-peer-review.pdf)
- [Respiratory Failure CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/respiratory-failure-cliff-2023.pdf)
- [Respiratory Failure, Acute CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/respiratory-failure-acute-cliff-peer-review.pdf)
- [Respiratory Infection CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/respiratory-infection-cliff.pdf)
- [Respiratory Tract Infection CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/respiratory-tract-infection-upper-cliff-2023.pdf)
- [Retinal Detachment CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/retinal-detachment-cliff.pdf)
- [Risk Assessment for Artemis 32 day Design Reference Missions (DRM)](https://www.nasa.gov/wp-content/uploads/2026/08/risk-assessment-for-artemis-32-day-design-reference-missions-drm.pdf)
- [Risk Assessment of ALS Ventilation for ISS and CCV](https://www.nasa.gov/wp-content/uploads/2026/08/risk-assessment-of-als-ventilation-for-iss-and-ccv.pdf)
- [Risk for Pregnancy CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/risk-for-pregnancy-cliff-peer-review.pdf)
- [Risk of Appendicitis and Cholecystitis versus Risk of Small Bowel Obstruction (SBO) following Prophylactic Surgery](https://www.nasa.gov/wp-content/uploads/2026/08/risk-of-appendicitis-and-cholecystitis-versus-risk-of-small-bowel-obstruction-sbo.pdf)
- [Risk of Appendicitis and Cholecystitis versus Risk of Small Bowel Obstruction following Prophylactic Surgery](https://www.nasa.gov/wp-content/uploads/2026/08/risk-of-appendicitis-and-cholecystitis-vs-risk-of-small-bowel-obstruction-following-phophylaxis.pdf)
- [SAS Constipation CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/sas-constipation-cliff-peer-review.pdf)
- [Seizures CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/seizures-cliff.pdf)
- [Seizures CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/seizures-cliff-2023.pdf)
- [Seizures CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/seizures-cliff-peer-review.pdf)
- [Sensorineural Hearing Loss CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/sensorineural-hearing-loss-cliff-peer-review.pdf)
- [Sepsis CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/sepsis-cliff.pdf)
- [Sepsis CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/sepsis-cliff-2023.pdf)
- [Sepsis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/sepsis-cliff-peer-review.pdf)
- [Severe Head Injury (Trauma) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-severe-head-cliff-2023.pdf)
- [Shock Cardiogenic CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/shock-cardiogenic-cliff-peer-review.pdf)
- [Shoulder Dislocation CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/shoulder-dislocation-cliff.pdf)
- [Shoulder Dislocation CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/dislocation-shoulder-cliff-2023.pdf)
- [Shoulder Dislocation CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/shoulder-dislocation-cliff-peer-review.pdf)
- [Shoulder Sprain/Strain CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/shoulder-sprain-strain-cliff.pdf)
- [Skin Abrasion CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/skin-abrasion-cliff.pdf)
- [Skin Abrasion CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/skin-abrasion-cliff-2023.pdf)
- [Skin Abrasion CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/skin-abrasions-cliff-peer-review.pdf)
- [Skin Infection (Bacterial) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/skin-infection-bacterial-cliff-2023.pdf)
- [Skin Infection CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/skin-infection-cliff.pdf)
- [Skin Infections to Viral/Fungal CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/skin-infections-%E2%80%93-viral-fungal-cliff-peer-review.pdf)
- [Skin Laceration CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/skin-laceration-cliff.pdf)
- [Skin Laceration CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/skin-laceration-cliff-2023.pdf)
- [Skin Rash (Spaceflight Associated) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/rash-spaceflight-associated-cliff-2023.pdf)
- [Skin Rash CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/skin-rash-cliff.pdf)
- [Sleep Disorder CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/sleep-disorder-cliff.pdf)
- [Sleep Disturbance CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/bhp-sleep-disturbance-cliff-2023.pdf)
- [Small Bowel Obstruction CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/small-bowel-obstruction-cliff.pdf)
- [Small Bowel Obstruction CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/small-bowel-obstruction-cliff-2023.pdf)
- [Small Bowel Obstruction CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/small-bowel-obstruction-cliff-peer-review.pdf)
- [Smoke Inhalation CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/smoke-inhalation-cliff.pdf)
- [Space Adaptation Back Pain CLIFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-back-pain-cliff-peer-review.pdf)
- [Space Adaptation Insomnia CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-insomnia-cliff-peer-review.pdf)
- [Space Adaptation Nasal Congestion CLIFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-nasal-congestion-cliff-peer-review.pdf)
- [Space Adaptation Space Motion Sickness CLIFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-space-motion-sickness-cliff-peer-review.pdf)
- [Space Adaptation Urinary Incontinence CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-urinary-incontinence-cliff-peer-review.pdf)
- [Space Adaptation Urinary Retention CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-urinary-retention-cliff-peer-review.pdf)
- [Space Motion Sickness (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/space-motion-sickness-space-adaptation-cliff.pdf)
- [Space Motion Sickness (Space Adaptation) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-space-motion-sickness-cliff-2023.pdf)
- [Spaceflight Associated Headache CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/spaceflight-associated-headache-cliff-peer-review.pdf)
- [Spaceflight Associated Neuro-Ocular Syndrom (SANS) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/space-flight-associated-neuro-ocular-syndrome-sans-cliff-2023.pdf)
- [Spaceflight Associated Neuro-ocular Syndrome CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/spaceflight-associated-neuro-ocular-cliff-peer-review.pdf)
- [Spaceflight Associated Rash CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/spaceflight-associated-rash-cliff-peer-review.pdf)
- [Spaceflight-Related Relationship Problems CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/spaceflight-related-problems-cliff-peer-review.pdf)
- [Sprain Strain Upper Extremity CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/sprain-strain-upper-extremity-peer-review.pdf)
- [Streptococcal Pharyngitis CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/streptococcal-pharyngitis-cliff-peer-review.pdf)
- [Stroke (Cerebrovascular Accident) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/stroke-cerebrovascular-accident-cliff.pdf)
- [Sudden Cardiac Arrest CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/sudden-cardiac-arrest-cliff.pdf)
- [Sudden Cardiac Arrest CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/sudden-cardiac-arrest-cliff-2023.pdf)
- [Sudden Cardiac Arrest CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/sudden-cardiac-arrest-cliff-peer-review.pdf)
- [Thoracic/Lumbar Spine Fracture CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/thoracic-lumbar-spine-fracture-cliff-peer-review.pdf)
- [Toxic Dermal Exposure CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-dermal-exposure-cliff-2023.pdf)
- [Toxic Exposure (Ammonia) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-exposure-ammonia-cliff.pdf)
- [Toxic Inhalation Exposure (Combustion Products) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-inhalation-exposure-combustion-products-cliff-2023.pdf)
- [Toxic Inhalation Exposure (Combustion Products) CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-inhalation-exposure-combustion-products-cliff-peer-review.pdf)
- [Toxic Inhalation Exposure CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-inhalation-exposure-cliff-2023.pdf)
- [Toxic Inhalation Exposure CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-inhalation-exposure-cliff-peer-review.pdf)
- [Training](https://www.nasa.gov/wp-content/uploads/2026/08/training.pdf)
- [Trauma-Chest Injury (Blunt) CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-chest-injury-blunt-cliff-peer-review.pdf)
- [Trauma-Minor Head CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-%E2%80%93-minor-head-cliff-peer-review.pdf)
- [Trauma-Severe Head CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-%E2%80%93-severe-head-cliff-peer-review.pdf)
- [Traumatic Hypovolemic Shock CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/traumatic-hypovolemic-shock-cliff.pdf)
- [Traumatic Hypovolemic Shock CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/traumatic-hypovolemic-shock-cliff-2023.pdf)
- [Traumatic Hypovolemic Shock CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/traumatic-hypovolemic-shock-cliff-peer-review.pdf)
- [Upper Extremity Sprain/Strain CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/sprain-strain-upper-extremity-cliff-2023.pdf)
- [Upper Extremity Tendinopathy Enthesopathy (Bursitis/Over Use Injuries) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/tendinopathy-enthesopathy-bursitis-over-use-injuries-upper-extremity-cliff-2023.pdf)
- [Upper Extremity Tendinopathy Enthesopathy (Bursitis/Over Use Injuries) CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/tendinopathy-enthesopathy-bursitis-over-use-injury-upper-extremity-cliff-peer-review.pdf)
- [Upper Respiratory Tract Infection CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/upper-respiratory-tract-infection-peer-review.pdf)
- [Urinary Incontinence (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/urinary-incontinence-space-adaptation-cliff.pdf)
- [Urinary Incontinence (Space Adaptation) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-urinary-incontinence-cliff-2023.pdf)
- [Urinary Retention (Space Adaptation) CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/urinary-retention-space-adaptation-cliff.pdf)
- [Urinary Retention (Space Adaptation) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/space-adaptation-urinary-retention-cliff-2023.pdf)
- [Urinary Tract Infection CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/urinary-tract-infection-cliff.pdf)
- [Urinary Tract Infection CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/urinary-tract-infection-cliff-2023.pdf)
- [Urinary Tract Infection CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/urinary-tract-infection-cliff-peer-review.pdf)
- [Vaginal Yeast Infection CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/vaginal-yeast-infection-cliff.pdf)
- [Vaginal Yeast Infection CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/vaginal-yeast-infection-cliff-2023.pdf)
- [Vaginal Yeast Infections CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/vaginal-yeast-infection-cliff-peer-review.pdf)
- [Venous Thromboembolism (VTE) CliFF (2023)](https://www.nasa.gov/wp-content/uploads/2026/08/venous-thromboembolism-cliff-2023.pdf)
- [Venous Thromboembolism (VTE) CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/venous-thromboembolism-cliff-peer-review.pdf)
- [Visual Impairment and/or Increased Intracranial Pressure (VIIP) (Space Adaptation CLiFF](https://www.nasa.gov/wp-content/uploads/2026/08/visual-impairment-and-or-increased-intracranial-pressure-viipspace-adaptation-cliff.pdf)
- [Wrist Fracture CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/wrist-fracture-cliff.pdf)
- [Wrist Fracture CliFF Peer Review](https://www.nasa.gov/wp-content/uploads/2026/08/wrist-fracture-cliff-peer-review.pdf)
- [Wrist Sprain/Strain CliFF](https://www.nasa.gov/wp-content/uploads/2026/08/wrist-sprain-strain-cliff.pdf)

<a id="hrr-conditions"></a>

### Human Research Roadmap: medical condition summaries

One evidence summary per medical condition from the Human Research Roadmap.

- [Abdominal Injury](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Abdominal_Injury.pdf)
- [Allergic Reaction (Mild to Moderate)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Allergic_Reaction_(Mild_to_Moderate).pdf)
- [Altitude Sickness](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Altitude_Sickness.pdf)
- [Anaphylaxis](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Anaphylaxis.pdf)
- [Aphthous Ulcer](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Aphthous_Ulcer.pdf)
- [Back Injury](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Back_Injury.pdf)
- [Back Pain (Space Adaptation)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Back_Pain_(Space_Adaptation).pdf)
- [Barotrauma (Ear/Sinus Block)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Barotrauma_(Ear_Sinus_Block).pdf)
- [Burns](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Burns.pdf)
- [Cardiogenic Shock](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Cardiogenic_Shock.pdf)
- [Cellulitis](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Cellulitis.pdf)
- [Chest Injury/Pneumothorax](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Chest_Injury_Pneumothorax.pdf)
- [Chest Pain/Angina](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Chest_Pain_Angina.pdf)
- [Choking/Obstructed Airway](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Choking_Obstructed_Airway.pdf)
- [Compartment Syndrome](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Compartment_Syndrome.pdf)
- [Constipation (Space Adaptation)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Constipation_(Space_Adaptation).pdf)
- [De Novo Cardiac Arrhythmia](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/De_Novo_Cardiac_Arrhythmia.pdf)
- [De Novo Hypertension](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/De_Novo_Hypertension.pdf)
- [Decompression Sickness](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Decompression_Sickness.pdf)
- [Dental: Abscess](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Dental_-_Abscess.pdf)
- [Dental: Avulsion/Tooth Loss](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Dental_-_Avulsion_Tooth_Loss.pdf)
- [Dental: Caries](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Dental_-_Caries.pdf)
- [Dental: Crown Replacement](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Dental_-_Crown_Replacement.pdf)
- [Dental: Exposed Pulp/Pulpitis](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Dental_-_Exposed_Pulp_Pulpitis.pdf)
- [Dental: Filling Replacement](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Dental_-_Filling_Replacement.pdf)
- [Diarrhea](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Diarrhea.pdf)
- [Elbow Dislocation](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Elbow_Dislocation.pdf)
- [Extremity Sprains/Strains](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Extremity_Sprains_Strains.pdf)
- [Eye Abrasion (Foreign Body)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Eye_Abrasion_(Foreign_Body).pdf)
- [Eye Chemical Burn](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Eye_Chemical_Burn.pdf)
- [Eye Corneal Ulcer](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Eye_Corneal_Ulcer.pdf)
- [Eye Infection](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Eye_Infection.pdf)
- [Eye Penetration (Foreign Body)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Eye_Penetration_(Foreign_Body).pdf)
- [Finger Dislocation](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Finger_Dislocation.pdf)
- [Fingernail Delamination (EVA)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Fingernail_Delamination_(EVA).pdf)
- [Glaucoma: Acute](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Glaucoma_-_Acute.pdf)
- [Head Injury](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Head_Injury.pdf)
- [Headache](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Headache.pdf)
- [Hemorrhoids](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Hemorrhoids.pdf)
- [Herpes Zoster/Simplex Reactivation](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Herpes_Zoster_Simplex_Reactivation.pdf)
- [Hip/Lower Extremity Fracture](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Hip_Lower_Extremity_Fracture.pdf)
- [Hypovolemic Shock](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Hypovolemic_Shock.pdf)
- [Indigestion](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Indigestion.pdf)
- [Insomnia (Early/Late)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Insomnia_(Early_Late).pdf)
- [Intra-Abdominal Infection](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Intra-Abdominal_Infection.pdf)
- [Lumbar Spine Fracture](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Lumbar_Spine_Fracture.pdf)
- [Malignancy](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Malignancy.pdf)
- [Nasal Congestion (Space Adaptation)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Nasal_Congestion_(Space_Adaptation).pdf)
- [Nausea/Vomiting](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Nausea_Vomiting.pdf)
- [Neck Injury](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Neck_Injury.pdf)
- [Nephrolithiasis](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Nephrolithiasis.pdf)
- [Neurogenic Shock](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Neurogenic_Shock.pdf)
- [Nosebleed (Space Adaptation)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Nosebleed_(Space_Adaptation).pdf)
- [Osteoporosis](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Osteoporosis.pdf)
- [Otitis Externa](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Otitis_Externa.pdf)
- [Otitis Media](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Otitis_Media.pdf)
- [Paresthesias/Hot Spots (EVA)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Paresthesias_Hot_Spots_(EVA).pdf)
- [Pharyngitis](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Pharyngitis.pdf)
- [Prostatitis](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Prostatitis.pdf)
- [Radiation Sickness](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Radiation_Sickness.pdf)
- [Respiratory Infection](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Respiratory_Infection.pdf)
- [Seizure](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Seizure.pdf)
- [Sepsis](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Sepsis.pdf)
- [Shoulder Dislocation](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Shoulder_Dislocation.pdf)
- [Sinusitis](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Sinusitis.pdf)
- [Skin Abrasion](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Skin_Abrasion.pdf)
- [Skin Laceration](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Skin_Laceration.pdf)
- [Skin Rash](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Skin_Rash.pdf)
- [Smoke Inhalation](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Smoke_Inhalation.pdf)
- [Space Motion Sickness (Space Adaptation)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Space_Motion_Sickness_(Space_Adaptation).pdf)
- [Stroke](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Stroke.pdf)
- [Sudden Cardiac Arrest](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Sudden_Cardiac_Arrest.pdf)
- [Surgical Treatment](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Surgical_Treatment.pdf)
- [Toxic Exposure](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Toxic_Exposure.pdf)
- [Upper Extremity Fracture](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Upper_Extremity_Fracture.pdf)
- [Urinary Incontinence (Space Adaptation)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Urinary_Incontinence_(Space_Adaptation).pdf)
- [Urinary Retention (Space Adaptation)](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Urinary_Retention_(Space_Adaptation).pdf)
- [Urinary Tract Infection](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Urinary_Tract_Infection.pdf)
- [Vaginal Yeast Infection](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Vaginal_Yeast_Infection.pdf)
- [Visual Impairment/Intracranial Hypertension](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions/Visual_Impairment_Intracranial_Hypertension.pdf)

<a id="hrr-evidence"></a>

### Human Research Roadmap: evidence reports and research plans

The risk evidence reports behind NASA's human research program, with integrated research plans.

- [Additional Evidence 2017 Integrated EVA Human Research Plan 12-19-16 website](https://humanresearchroadmap.nasa.gov/Evidence/other/2017_Integrated_EVA_Human_Research_Plan_12-19-16_website.pdf)
- [Additional Evidence Team Additional Evidence](https://humanresearchroadmap.nasa.gov/Evidence/other/Team%20Additional%20Evidence.pdf)
- [Additional Info-Historical Acute Risk](https://humanresearchroadmap.nasa.gov/Evidence/other/Acute.pdf)
- [Additional Info-Historical Osteo Risk](https://humanresearchroadmap.nasa.gov/Evidence/other/Osteo.pdf)
- [Artificial Gravity Countermeasure Evidence Report](https://humanresearchroadmap.nasa.gov/Evidence/other/AG%20Evidence%20Report.pdf)
- [behavioral-health evidence report](https://humanresearchroadmap.nasa.gov/evidence/reports/HRP-ER-BMED-HFBP-20250210.pdf)
- [Bone Fracture Evidence Report 07-18-2024](https://humanresearchroadmap.nasa.gov/Evidence/reports/Bone%20Fracture%20Evidence%20Report_07-18-2024.pdf)
- [Cardio Risk EB 2022 FINAL 4-3-2023](https://humanresearchroadmap.nasa.gov/Evidence/reports/Cardio%20Risk%20EB%202022%20FINAL_4-3-2023.pdf)
- [Central Nervous System, Behavioral Health, and Sensorimotor (CBS) Integrated Research Plan: Implementation Strategy](https://humanresearchroadmap.nasa.gov/Evidence/other/Central%20Nervous%20System,%20Behavioral%20Health,%20and%20Sensorimotor%20(CBS)%20Integrated%20Research%20Plan%20Implementation%20Strategy.pdf)
- [Central Nervous System, Behavioral Health, and Sensorimotor (CBS) Integrated Research Plan: Problem Statement](https://humanresearchroadmap.nasa.gov/Evidence/other/Central%20Nervous%20System,%20Behavioral%20Health,%20and%20Sensorimotor%20(CBS)%20Integrated%20Research%20Plan%20Problem%20Statement.pdf)
- [Dust](https://humanresearchroadmap.nasa.gov/Evidence/reports/Dust.pdf)
- [Evidence report aerobic and muscle FINAL 4-3-2024](https://humanresearchroadmap.nasa.gov/Evidence/reports/Evidence%20report%20aerobic%20and%20muscle_FINAL%204-3-2024.pdf)
- [Evidence Report Hypoxia and DCS FINAL 1-8-2024](https://humanresearchroadmap.nasa.gov/Evidence/reports/Evidence%20Report%20Hypoxia%20and%20DCS_FINAL_1-8-2024.pdf)
- [Evidence Report-Appendix A_DAG Content](https://humanresearchroadmap.nasa.gov/Evidence/other/Appendix%20A_DAG%20Content.pdf)
- [Evidence Report-Appendix B_Renal ConOps](https://humanresearchroadmap.nasa.gov/Evidence/other/Appendix%20B_Renal%20ConOps_Final%20For%20SMOCB%20Review.pdf)
- [Evidence Report-DCS/Hypoxia](https://humanresearchroadmap.nasa.gov/Evidence/other/Evidence%20Report%20Hypoxia%20and%20DCS_FINAL_1-8-2024.pdf)
- [Evidence Report-EVA](https://humanresearchroadmap.nasa.gov/Evidence/other/EVA%20Injury%20Evidence%20Report%20FINAL_6-14-2023.pdf)
- [Evidence Report-Food](https://humanresearchroadmap.nasa.gov/Evidence/other/Food%20HRP-Evidence%20Report%20FINAL_4-15-24.pdf)
- [Evidence Report-Nutrition](https://humanresearchroadmap.nasa.gov/Evidence/other/Nutrition%20ER_human_adaptation_2020_final.pdf)
- [Evidence Report-Pharm 2024](https://humanresearchroadmap.nasa.gov/Evidence/other/2024%20Renal%20Evidence%20Report_Final.pdf)
- [Evidence Report-Pharm, 2011 (HRR Gaps Pharm-301, Pharm-601)](https://humanresearchroadmap.nasa.gov/Evidence/other/Pharm_2011.pdf)
- [Evidence Report-Pharm, 2023 (HRR Gaps Pharm-101 to 501)](https://humanresearchroadmap.nasa.gov/Evidence/other/Pharm%20evidence%20report_12-20-23_FINAL.pdf)
- [EvidenceBook](https://humanresearchroadmap.nasa.gov/Evidence/reports/EvidenceBook.pdf)
- [Host Micro EB Final 7-6-22](https://humanresearchroadmap.nasa.gov/Evidence/reports/Host_Micro%20EB_Final%207-6-22.pdf)
- [HRP-ER-BMED-HFBP-20250210](https://humanresearchroadmap.nasa.gov/Evidence/reports/HRP-ER-BMED-HFBP-20250210.pdf)
- [HRP-ER-CANCER-SR-20260811](https://humanresearchroadmap.nasa.gov/Evidence/reports/HRP-ER-CANCER-SR-20260811.pdf)
- [HRR: Evidence](https://humanresearchroadmap.nasa.gov/Evidence/)
- [Human Research Program Human Health Countermeasures Element Approved for Public Release: January 22, 2023](https://humanresearchroadmap.nasa.gov/evidence/reports/Evidence%20report%20aerobic%20and%20muscle_FINAL%204-3-2024.pdf)
- [IMMUNE EB Update 2021 v02 FINAL 6-15-22](https://humanresearchroadmap.nasa.gov/Evidence/reports/IMMUNE%20EB%20Update%202021%20v02%20FINAL_6-15-22.pdf)
- [IVD](https://humanresearchroadmap.nasa.gov/Evidence/reports/IVD.pdf)
- [Medical](https://humanresearchroadmap.nasa.gov/evidence/reports/Medical.PDF)
- [Medical](https://humanresearchroadmap.nasa.gov/Evidence/reports/Medical.pdf)
- [Medical Conditions](https://humanresearchroadmap.nasa.gov/Evidence/medicalConditions.aspx)
- [Multiple Evidence Reports](https://humanresearchroadmap.nasa.gov/Evidence/reports/Multiple%20Evidence%20Reports.pdf)
- [OP Dynamic Loads EB FINAL 11-30-22](https://humanresearchroadmap.nasa.gov/Evidence/reports/OP%20Dynamic%20Loads%20EB%20FINAL_11-30-22.pdf)
- [Risk of Inadequate HSIA approved FINAL 6-13-2023](https://humanresearchroadmap.nasa.gov/Evidence/reports/Risk%20of%20Inadequate%20HSIA%20approved%20FINAL_%206-13-2023.pdf)
- [SANS](https://humanresearchroadmap.nasa.gov/evidence/reports/SANS.pdf)
- [SANS EB 2022 FINAL 8-15-22](https://humanresearchroadmap.nasa.gov/Evidence/reports/SANS%20EB%202022%20FINAL_8-15-22.pdf)
- [Sleep](https://humanresearchroadmap.nasa.gov/Evidence/reports/Sleep.pdf)
- [SM Evidence Report 2022 FINAL 6-13-2023](https://humanresearchroadmap.nasa.gov/Evidence/reports/SM%20Evidence%20Report%202022_FINAL%206-13-2023.pdf)
- [Team 2022 -Evidence Report FINAL 5-9-2024](https://humanresearchroadmap.nasa.gov/Evidence/reports/Team%202022%20-Evidence%20Report%20FINAL_5-9-2024.pdf)

<a id="risks"></a>

### Human health and performance risks

NASA's risk pages, one per spaceflight risk.

- [Concern of Venous Thromboembolism](https://www.nasa.gov/directorates/esdmd/hhp/concern-of-venous-thromboembolism/)
- [Risk of Adverse Cognitive or Behavioral Changes and Psychiatric Disorders Leading to In-mission Health and Performance and Long-term Health Effects (Behavioral Health Risk)](https://www.nasa.gov/directorates/esdmd/hhp/behavioral-health-risk/)
- [Risk of Adverse Health Effects Due to Host-Microorganism Interactions](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-adverse-health-effects-due-to-host-microorganism-interactions/)
- [Risk of Adverse Health Outcomes and Decrements in Performance due to Inflight Medical Conditions](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-adverse-health-outcomes-and-decrements-in-performance-due-to-inflight-medical-conditions/)
- [Risk of Adverse Health Outcomes and Performance Decrements resulting from Non-Ionizing Radiation (NIR) during Spaceflight (Non-Ionizing Radiation Risk)](https://www.nasa.gov/directorates/esdmd/hhp/non-ionizing-radiation-risk/)
- [Risk of Adverse In-Mission Health and Performance Effects and Long-Term Health Effects Due to Celestial Dust Exposure (Dust Risk)](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-adverse-health-performance-effects-of-celestial-dust-exposure/)
- [Risk of Adverse Mission Outcomes Due to Earth Independent Human-Systems Operations (EIHSO Risk)](https://www.nasa.gov/directorates/esdmd/hhp/eihso-risk/)
- [Risk of Altered Sensorimotor/Vestibular Function Impacting Critical Mission Tasks, Human Health, and Long-Term Health (Sensorimotor Risk)](https://www.nasa.gov/directorates/esdmd/hhp/sensorimotor-risk/)
- [Risk of Bone Fracture due to Spaceflight-induced Changes to Bone](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-bone-fracture-due-to-spaceflight-induced-changes-to-bone/)
- [Risk of Cardiovascular Adaptations Contributing to Adverse Mission Performance and Health Outcomes (CV Risk)](https://www.nasa.gov/directorates/esdmd/hhp/cv-risk/)
- [Risk of Hearing Loss and Performance Decrements Due to Acoustics Issues in Space](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-hearing-loss-and-performance-decrements-due-to-acoustics-issues-in-space/)
- [Risk of In Mission Impacts, Adverse Health Events or Long-Term Health Impacts due to Altered Immune Response (Immune Risk)](https://www.nasa.gov/directorates/esdmd/hhp/immune-risk/)
- [Risk of In-Mission Injury and Performance Decrements and Long-term Health Effects due to Dynamic Loads (Dynamic Loads Risk)](https://www.nasa.gov/directorates/esdmd/hhp/dynamic-loads-risk/)
- [Risk of In-Mission Performance and Behavioral Health Decrements Due to Inadequate Cooperation, Coordination, Communication, and Psychosocial Adaptation within a Team (Team Risk)](https://www.nasa.gov/directorates/esdmd/hhp/team-risk/)
- [Risk of Inadequate Food and Nutrition Causing Decrements in In-Mission Health and Performance and Long-term Health (Food and Nutrition Risk)](https://www.nasa.gov/directorates/esdmd/hhp/food-and-nutrition-risk/)
- [Risk of Ineffective or Toxic Medications During Long-Duration Exploration Spaceflight](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-ineffective-or-toxic-medications-during-long-duration-exploration-spaceflight/)
- [Risk of Mission Impacting Injury and Compromised Performance and Long-Term Health Effects due to EVA Operations (EVA Risk)](https://www.nasa.gov/directorates/esdmd/hhp/eva-operations-risk/)
- [Risk of Mission Impacts and Long-Term Health Issues due to Decompression Sickness](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-decompression-sickness/)
- [Risk of Nominal Acute and Chronic Ambient Carbon Dioxide Exposure in Crewed Vehicles Leading to In-Mission Health Effects or Performance Decrements (CO2 Risk)](https://www.nasa.gov/directorates/esdmd/hhp/carbon-dioxide-risk/)
- [Risk of Performance Decrements and Adverse Health Outcomes Resulting from Sleep Loss, Circadian Desynchronization, and Work Overload](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-performance-decrements-and-adverse-health-outcomes-resulting-from-sleep-loss-circadian-desynchronization-and-work-overload/)
- [Risk of Radiation Carcinogenesis](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-radiation-carcinogenesis/)
- [Risk of Reduced Crew Health and Performance Due to Hypoxia](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-reduced-crew-health-and-performance-due-to-hypoxia/)
- [Risk of Reduced Physical Performance Capabilities Due to Reduced Aerobic Capacity (Aerobic Risk)](https://www.nasa.gov/directorates/esdmd/hhp/aerobic-risk/)
- [Risk of Reduced Physical Performance Capabilities Due to Reduced Muscle Size, Strength, and Endurance (Muscle Risk)](https://www.nasa.gov/directorates/esdmd/hhp/muscle-risk/)
- [Risk of Renal Stone Formation](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-renal-stone-formation/)
- [Risk of Spaceflight Associated Neuro-ocular Syndrome (SANS Risk)](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-spaceflight-associated-neuro-ocular-syndrome/)
- [Risk of Toxic Substance Exposure](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-toxic-substance-exposure/)
- [Risk of Urinary Retention](https://www.nasa.gov/directorates/esdmd/hhp/risk-of-urinary-retention/)
- [Risk to Crew Health Due to Electrical Shock (Electrical Shock Risk)](https://www.nasa.gov/directorates/esdmd/hhp/electrical-shock-risk/)
- [Risk to Vehicle Crew Egress Capability and Task Performance as Applied to Earth and Extraterrestrial Landings](https://www.nasa.gov/directorates/esdmd/hhp/risk-to-vehicle-crew-egress-capability-and-task-performance-as-applied-to-earth-and-extraterrestrial-landings/)

<a id="briefs"></a>

### OCHMO technical and medical briefs

Short NASA briefs on one topic each. They state that they are for reference and do not give medical recommendations for individuals.

- [Behavioral Health and Performance](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-016-behavioral-health.pdf)
- [Bone Loss](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-030-bone-loss.pdf)
- [Circadian Desynchrony](https://www.nasa.gov/wp-content/uploads/2026/03/ochmo-mtb-009-circadian-desynchrony.pdf)
- [Cognitive Workload](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-032-cognitive-workload.pdf)
- [Crew Medical Support for Entry, Descent, and Landing](https://www.nasa.gov/wp-content/uploads/2026/05/ochmo-tb-051-crew-medical-support-entry-descent-landing.pdf)
- [Crew Selection and Recertification](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-034-crew-selection-and-recertification.pdf)
- [Decompression Sickness (DCS)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-037-decompression-sickness.pdf)
- [Executive Summary ochmo-mtb-004-space-adaptation-sickness-sas](https://www.nasa.gov/wp-content/uploads/2025/09/ochmo-mtb-004-space-adaptation-sickness-sas.pdf)
- [Exercise Overview](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-031-exercise-overview.pdf)
- [Food and Nutrition](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-013-food-and-nutrition.pdf)
- [Fundamentals of Human Health](https://www.nasa.gov/reference/fundamentals-of-human-health/)
- [Health Stabilization Program](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-006-health-stabilization-program.pdf)
- [Helicobacter pylori (H. pylori)](https://www.nasa.gov/wp-content/uploads/2025/09/ochmo-mtb-005-h-pylori.pdf)
- [Human Spaceflight and Aviation Standards](https://www.nasa.gov/ochmo/hsa-standards/)
- [Human Spaceflight Standards](https://www.nasa.gov/ochmo/human-spaceflight-and-aviation-standards/)
- [Longitudinal Health Surveillance](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-043-longitudinal-health-surveillance.pdf)
- [Medical Operations](https://www.nasa.gov/wp-content/uploads/2025/08/ochmo-tb-044-medical-operations.pdf)
- [Mission Duration](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-007-mission-duration.pdf)
- [Mortality Related to Human Spaceflight](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-012-mortality-related-to-human-spaceflight-.pdf)
- [NASA Office of the Chief Health & Medical Officer (OCHMO)](https://www.nasa.gov/wp-content/uploads/2023/03/medical-care-technical-brief-ochmo.pdf)
- [NASA technical briefs](https://www.nasa.gov/ochmo/hsa-standards/ochmo-technical-briefs/)
- [Non-Ionizing Radiation](https://www.nasa.gov/wp-content/uploads/2024/02/ochmo-tb-045-non-ionizing-radiation.pdf)
- [OCHMO Technical and Medical Brief Collections](https://www.nasa.gov/reference/ochmo-technical-and-medical-brief-collections/)
- [OCHMO-MTB-002 Waivered Health Conditions](https://www.nasa.gov/ochmo/hsa-standards/ochmo-technical-briefs/waivered-health-conditions/)
- [ochmo-mtb-007-venous-thrombosis-in-spaceflight](https://www.nasa.gov/wp-content/uploads/2026/01/ochmo-mtb-007-venous-thrombosis-in-spaceflight.pdf)
- [Orthostatic Intolerance](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-019-orthostatic-intolerance.pdf)
- [Patent Foramen Ovale](https://www.nasa.gov/wp-content/uploads/2026/11/ochmo-mtb-011-patent-foramen-ovale-pfo.pdf)
- [Pharmaceuticals and Medications](https://www.nasa.gov/wp-content/uploads/2026/01/ochmo-mtb-012-pharmaceutical-care.pdf)
- [Sensorimotor](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-010-sensorimotor.pdf)
- [Shoulder Injury](https://www.nasa.gov/wp-content/uploads/2025/09/ochmo-mtb-006-shoulder-injury.pdf)
- [Sleep brief](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-041-sleep-accommodations.pdf)
- [Spaceflight Associated Neuro-ocular Syndrome (SANS)](https://www.nasa.gov/wp-content/uploads/2024/02/ochmo-mtb-001-sans.pdf)
- [Spaceflight Experience and Medical Care](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-033-spaceflight-experience-medical-care.pdf)
- [Spaceflight Toxicology](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-015-spaceflight-toxicology.pdf)
- [Strength & Performance for EVA](https://www.nasa.gov/wp-content/uploads/2026/07/guidance-for-strength-and-human-performance-on-extraterrestrial-surface-eva-07222026.pdf)
- [Suited CO2](https://www.nasa.gov/wp-content/uploads/2025/02/suited-co2-technical-bulletin-02182025.pdf)
- [Urinary Health](https://www.nasa.gov/wp-content/uploads/2025/01/ochmo-mtb-003-urinary-health.pdf)

<a id="standards"></a>

### Standards and medical checklists

NASA-STD-3001 and its appendices, and flight medical checklists.

- [3.0 Health and Medical Care](https://www.nasa.gov/reference/3-0-health-and-medical-care/)
- [Development of an Accepted Medical Condition List for Exploration Medical Capability Scoping](https://ntrs.nasa.gov/api/citations/20190027540/downloads/20190027540.pdf)
- [International Space Station Integrated Medical Group (IMG) Medical Checklist](https://newspaceeconomy.ca/wp-content/uploads/2022/11/163533main_iss_med_cl.pdf)
- [Medical Checklist](https://www.ibiblio.org/apollo/Shuttle/Medical%20Checklist/Medical%20Checklist%20Rev%20K%20PCN-4%20E1.pdf)
- [NASA Spaceflight Human System Standard Volume 1: Crew Health](https://www.nasa.gov/reference/nasa-std-3001v1/)
- [NASA-STD-3001 Appendix E](https://www.nasa.gov/reference/appendix-e-health-and-medical-care-technical-requirements/)
- [NASA-STD-3001 Vol 1 Rev C with Signature](https://standards.nasa.gov/sites/default/files/standards/NASA/C/nasa-std-3001-vol-1-rev-c-signature.pdf)

<a id="models"></a>

### Condition lists and mission medical risk models

How NASA chose the conditions it models for exploration missions, and the IMM and IMPACT tools that use them.

- [ARTEMIS II MISSION MEDICAL RISK: A COMPARISON OF THE IMPACT AND IMM MODELING TOOLS](https://ntrs.nasa.gov/api/citations/20250002759/downloads/AsMA%202025_IMMvsIMPACT%20Artemis%20II_Final.pdf)
- [Artificial Intelligence (AI) Methods for Augmenting the IMPACT Tool Evidence Library: NASA Technical Reports Server (NTRS)](https://ntrs.nasa.gov/citations/20240011623)
- [Artificial Intelligence (AI) Methods for Automating the Impact Tool Evidence Library: NASA Technical Reports Server (NTRS)](https://ntrs.nasa.gov/citations/20240012395)
- [Decision Framework for Classifying the State Of Knowledge for Pharmaceutical Stability In Spaceflight](https://ntrs.nasa.gov/api/citations/20240003454/downloads/AsMA%202024%20System%20Levers%20Intro.pdf)
- [Exploration Medical Capability](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf)
- [HRR: Task: Informing Mission Planning via Analysis of Complex Tradespaces (IMPACT) V 1.x](https://humanresearchroadmap.nasa.gov/tasks/task.aspx?i=2607)
- [IMM documentation](https://www.nasa.gov/integrated-medical-model-documentation/)
- [LSAH: NLSP](https://nlsp.nasa.gov/resources/lsah)
- [Quantifying Medical Risk on a Long Duration Lunar Mission: A Demonstration of NASA’s IMPACT Tradespace Analysis Tool: NASA Technical Reports Server (NTRS)](https://ntrs.nasa.gov/citations/20240011063)
- [Risk of Adverse Health Outcomes and Decrements in Performance due to In-Flight Medical Conditions](https://ntrs.nasa.gov/api/citations/20170004604/downloads/20170004604.pdf)
- [Selecting Medical Conditions Relevant to Exploration Spaceflight to Create the IMPACT 1.0 Medical Condition List](https://doi.org/10.3357/amhp.6199.2023)
- [Selecting Medical Conditions Relevant to Exploration Spaceflight to Create the IMPACT 1.0 Medical Condition List](https://pubmed.ncbi.nlm.nih.gov/37349929/)
- [Selection of Next Priority IMPACT Medical Conditions Based on Available Terrestrial and Spaceflight Data: NASA Technical Reports Server (NTRS)](https://ntrs.nasa.gov/citations/20220015657)

<a id="observed"></a>

### Illness observed in flight and long-term health

What crews have actually had in flight, and long-term surveillance. Some of these are papers or reviews outside NASA.

- [Congestion and Sinonasal Illness in Outer Space: A Study on the International Space Station](https://pmc.ncbi.nlm.nih.gov/articles/PMC12322577/)
- [EvidenceBook](https://humanresearchroadmap.nasa.gov/evidence/reports/EvidenceBook.pdf)
- [Frequency and Clinical Features of Space Headache Experienced by Astronauts During Long-Haul Space Flights , Neurology](https://www.neurology.org/doi/10.1212/WNL.0000000000209224)
- [Health Risks: Health Standards for Long Duration and Exploration Spaceflight: NCBI Bookshelf](https://www.ncbi.nlm.nih.gov/books/NBK222157/)
- [Houston Methodist Teams Up with NASA to Probe Spaceflight's Impact on Sinonasal Health](https://www.houstonmethodist.org/leading-medicine-blog/articles/2025/aug/houston-methodist-teams-up-with-nasa-to-probe-spaceflights-impact-on-sinonasal-health/)
- [Human Health during Space Travel: State-of-the-Art Review](https://www.mdpi.com/2073-4409/12/1/40)
- [Incidence of clinical symptoms during long-duration orbital spacefligh , IJGM , Dove Medical Press](https://www.dovepress.com/incidence-of-clinical-symptoms-during-long-duration-orbital-spacefligh-peer-reviewed-fulltext-article-IJGM)
- [Lifetime Surveillance of Astronaut Health (LSAH)](https://ntrs.nasa.gov/api/citations/20170001834/downloads/20170001834.pdf)
- [Managing Risks to Astronaut Health: Safe Passage: NCBI Bookshelf](https://www.ncbi.nlm.nih.gov/books/NBK223777/)
- [Ocular complaints and diagnoses in spaceflight](https://pmc.ncbi.nlm.nih.gov/articles/PMC10762130/)
- [Part 1: The Space Radiation Environment](https://ntrs.nasa.gov/api/citations/20250004252/downloads/Book%20Chapter.pdf)
- [Red risks for a journey to the red planet: The highest priority human health risks for a mission to Mars](https://spaceradiation.larc.nasa.gov/articles/2020/s41526-020-00124-6.pdf)
- [Risks to Astronaut Health During Space Travel: Safe Passage: NCBI Bookshelf](https://www.ncbi.nlm.nih.gov/books/NBK223785/)
- [Spaceflight Standard Measures is a multidisciplinary study that systematically monitors risks to astronaut health and performance](https://pmc.ncbi.nlm.nih.gov/articles/PMC12612093/)
- [TABLE 3 to 1, In-Flight Medical Events for U.S. Astronauts During the Space Shuttle Program (STS-1 through STS-89, April 1981 to January 1998): Safe Passage: NCBI Bookshelf](https://ncbi.nlm.nih.gov/books/NBK223777/table/ttt00006/?report=objectonly)
- [The National Academies of SCIENCES • ENGINEERING • MEDICINE: Review of NASA's Evidence Reports on Human Health Risks: NCBI Bookshelf](https://www.ncbi.nlm.nih.gov/books/NBK424967/)
