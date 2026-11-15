# NASA data in this package

Every file here is real NASA data (or data from NASA-authored papers) that a named part of the product uses. Each data file has a provenance JSON beside it with the source URL, documentation, retrieval time, byte count, sha256, mission and instrument, units per column (UCUM), preprocessing, license, the label REPLAY, what the data is not, and its consumers. Catalog IDs point to [NASA-SOURCES.md](../NASA-SOURCES.md). Nothing was interpolated, imputed or unit-converted in the files; the numbers in the "What the data shows" section below are computed from the files and say how.

Rule for every consumer: the data is replayed with its original dates and labeled Replay. It is another mission's cabin or another person's blood, never a crew member of this product.

## Datasets

| Category | File | Contents | Span and cadence | Rows | Catalog | Consumers |
|---|---|---|---|---|---|---|
| habitat-environment | [eda_rr1_telemetry.csv](habitat-environment/eda_rr1_telemetry.csv) | ISS cabin and ground temperature (Cel), relative humidity (%) and CO2 ([ppm]) during Rodent Research 1 | 2014-09-20 03:59 to 2014-11-01 03:54, mostly 300 s | 12,087 | N02 | E23 CO2, temperature and humidity feeds (primary replay, contract examples reading-co2-replay and series-co2-replay); E24 CO2 warning, positive case (one-hour mean 4,606 ppm); E21 demo replay clock 2014-10-07 15:36 |
| habitat-environment | [eda_rr5_telemetry.csv](habitat-environment/eda_rr5_telemetry.csv) | Same channels during Rodent Research 5 | 2017-06-01 05:00 to 2017-08-08 12:57, mostly 300 s | 19,160 | N02 | E23 CO2, temperature and humidity feeds (second profile); E24 negative control: highest one-hour mean 3,916 ppm, just under 3 mmHg, so no CO2 warning; RH up to 73% exercises the humidity band; E21 no-warning scenario |
| habitat-environment | [iss_air_inc71_mgsc_concentrations.csv](habitat-environment/iss_air_inc71_mgsc_concentrations.csv) | Grab-sample concentrations (mg/m3) of 38 compounds, incl. CO, alcohols, siloxanes, CO2 | 10 samples on 6 dates, 2024-05-08 to 2024-09-11 (incl. Starliner ascent and NG-21 ingress) | 410 | N31 | E24 VOC toxicity (T-value) golden case; E23 nominal CO and VOC levels for the SIM generators |
| habitat-environment | [iss_air_inc71_mgsc_t_values.csv](habitat-environment/iss_air_inc71_mgsc_t_values.csv) | NASA's T-values per compound and sample, with totals | Same 10 samples | 480 | N31 | E24 expected answers for the T-value golden case |
| habitat-environment | [iss_air_inc71_anita2_monthly.csv](habitat-environment/iss_air_inc71_anita2_monthly.csv) | ANITA-2 monthly average concentrations of 17 analytes (CO, ammonia, alcohols, siloxanes, refrigerants) | 2024-03 to 2024-09, monthly, plus the increment average | 136 | N31 | E23 CO and ammonia feed anchors; E24 CO and ammonia limits stay at info |
| water-quality | [iss_potable_water_inc71.csv](water-quality/iss_potable_water_inc71.csv) | Potable water archive samples: TOC, iodine, metals, volatiles, pH, conductivity, with NASA's limits | 3 samples: 2024-04-02, 2024-04-24, 2024-09-10 | 54 | N31 | E24 water quality limits golden case; E25 recorded water sample check |
| acoustics | [iss_acoustic_levels.csv](acoustics/iss_acoustic_levels.csv) | Module and sleep station sound levels (dBA, NC, SIL) and crew-worn LEQ16 and LEQ8 | Measurements dated 2020-04 to 2024-04; one row per stated value | 33 | N32, N33 | E23 noise feed levels per area; E24 noise warning golden inputs (LEQ16 73.0 dBA above the 72 dBA limit, hearing protection ordered); E21 noise scenario |
| radiation | [radlab_dostel_sample.csv](radiation/radlab_dostel_sample.csv) | DosTel1 and DosTel2 absorbed dose rate (uGy/h), Columbus | 2022-04-01 23:00 to 2022-04-02 01:04, about 100 s | 180 | N20 | E23 radiation feed ingest; contract example series-rad-replay (kept unchanged) |
| radiation | [radlab_iss_2017-09_spe.csv](radiation/radlab_iss_2017-09_spe.csv) | Every ISS instrument RadLab returned: DosTel1, DosTel2 (about 100 s, with position and flux), RAD and IV-TEPC daily values | 2017-09-03 00:00 to 2017-09-16 23:59, with the solar particle event of 10 and 11 September | 19,527 | N20 | E23 radiation feed and the simulator's SPE scenario; E24 radiation watch rule; E21 case spe-2017-09-iss-columbus |
| radiation | [cdaweb_iss_tepc_2017-09_spe.csv](radiation/cdaweb_iss_tepc_2017-09_spe.csv) | ISS TEPC dose (uGy/min) and dose equivalent (uSv/min) with position, UTC, no header row (columns in provenance) | 2017-09-03 00:00 to 2017-09-16 23:59 UTC, about 1 min | 20,035 | N30 | E23 second instrument, uGy and uSv; E24 mission dose tally; E14 daily dose series with one outlier day; E21 SPE case |
| crew-physiology | [i4_osd569_cbc.csv](crew-physiology/i4_osd569_cbc.csv) | Inspiration4 complete blood count, 23 analytes, lab reference ranges, coded crew C001 to C004 | 7 draws: L-92, L-44, L-3, R+1, R+45, R+82, R+194 (days) | 553 | N34 | E14 personal baselines and return-to-baseline on real numbers; E21 lab record entry with NA cells |
| crew-physiology | [i4_osd575_cmp.csv](crew-physiology/i4_osd575_cmp.csv) | Inspiration4 comprehensive metabolic panel, 19 analytes (glucose, electrolytes, renal, liver) | Same 7 draws | 531 | N34 | E14 personal baselines for electrolytes, renal function, liver chemistry, glucose; E21 lab record entry |

Scripts kept with the data: [transcribe_tox_inc71.py](habitat-environment/transcribe_tox_inc71.py) rebuilds the four Increment 71 tables from the NASA PDF (needs `pdftotext` from poppler); [build_iss_acoustic_levels.py](acoustics/build_iss_acoustic_levels.py) rebuilds the acoustic rows and checks every quote against the two PDFs. Both use the standard library only and download from the NASA URLs in the provenance files.

## Known-outcome cases

Each case folder has an `outcome.json` with the documented outcome quoted verbatim (source, page, URL), the onset relative to the data, the product output that should correspond, and the consuming epics. Quantitative cases replay a time series; qualitative cases hold printed values only, with no number read from a figure.

| Case | Kind | Input data | Documented outcome | Consumers |
|---|---|---|---|---|
| [spe-2017-09-iss-columbus](cases/spe-2017-09-iss-columbus/outcome.json) | quantitative | The two 2017 radiation files above (not copied) | Solar particle event (GLE 72) reached the ISS about 2017-09-11 04:30 UTC; dose rose at high latitudes on 11 and 12 September (Berger et al. 2018, N40). Expected: radiation watch, not emergency | E23, E24, E14, E21 |
| [spe-2017-09-mars-surface](cases/spe-2017-09-mars-surface/outcome.json) | quantitative | [MSL RAD surface dose rate](cases/spe-2017-09-mars-surface/radlab_msl_rad_surface_20170905_20170919.csv), 1,296 rows, 2017-09-05 00:16 to 2017-09-19 23:43, about 1,025 s | Radiation on the Mars surface more than doubled for over two days (JPL 2017-254, N39; Berger et al. 2018, N40). Expected: watch, and act for anyone outside | E23, E24, E14, E21 |
| [apollo13-lm-co2-cold-uti](cases/apollo13-lm-co2-cold-uti/outcome.json) | qualitative | 12 printed values: CO2 up to an indicated 14.9 mmHg, cabin temperature, water, sleep | Improvised scrubber; fatigue; urinary tract infection diagnosed postflight (N35, N36) | E23, E24, E28, E32, E21 |
| [apollo15-cardiac-arrhythmia-potassium](cases/apollo15-cardiac-arrhythmia-potassium/outcome.json) | qualitative | 8 printed events with times and approximate heart rates | Nodal bigeminy at 179:07 GET; potassium deficit found postflight (N36) | E07, E32, E21 |
| [iss-co2-symptom-reports-2001-2009](cases/iss-co2-symptom-reports-2001-2009/outcome.json) | qualitative | 4 events with the CO2 level as printed | Headache and other CO2 symptoms reported (NASA/TP-2010-216126, N38; C25) | E23, E24, E21 |
| [iss-sans-viip-cases-2012](cases/iss-sans-viip-cases-2012/outcome.json) | qualitative | 7 cases: in-flight vision change and onset as printed | Postflight optic disc edema, globe flattening, choroidal folds (NASA evidence report 2012, N37) | E25, E28, E21 |
| [iss-ijv-thrombosis-anticoagulant-supply](cases/iss-ijv-thrombosis-anticoagulant-supply/outcome.json) | qualitative | 7 rows: enoxaparin stock, dose per kg, day-33 reduction, day-42 apixaban | Internal jugular vein thrombosis treated aboard with a stretched supply (N42, N41) | E32, E28, E21 |

## What the data shows (computed from the files)

- **CO2 one-hour mean.** Mean of the samples in the hour ending at each sample (twelve five-minute samples), as PACKAGE-CHECKS.md does. RR-1 peaks at 4,606 ppm (2014-10-07 15:31 to 15:36); RR-5 peaks at 3,916 ppm. At an assumed 760 mmHg, 3 mmHg is 3,947 ppm.
- **Solar particle event, hourly profile.** DosTel2 readings outside a box around the South Atlantic Anomaly (latitude -55 to 5, longitude -95 to 20), averaged per hour and divided by the median hourly mean of 3 to 9 September (5.03 uGy/h). The rise starts in the 04:00 hour of 11 September (2.1x), reaches 6.6x at 05:00 and stays at 4.3x to 7.2x through 12:00 (peak 36.3 uGy/h at 07:00), except 06:00 (1.1x, a low-latitude orbit segment); 2.3x at 13:00 and back to baseline at 14:00. About 9 to 10 elevated hours, plus 2.4x at 12:00 on 12 September. The same method on the TEPC file (baseline 5.64 uGy/h) gives a peak of 106.8 uGy/h at 09:00 UTC (18.9x). So the real peak factor is about 7 at DosTel2 in Columbus and about 19 at the TEPC, the rise takes about one hour and the event lasts about 10 hours at the ISS. The raw maxima in both files are daily SAA passes, not the event.
- **Daily dose.** Each TEPC rate times its TIME_INTERVAL (minutes), summed over each UTC day: about 410 uGy (about 900 uSv) on quiet days, 829.7 uGy (1,710.4 uSv) on 11 September and 488.9 uGy (1,057.8 uSv) on 12 September. DosTel2 outside the SAA box averages about 5.1 uGy/h on 3 to 10 September, 16.7 on 11 September and 5.7 on 12 September. The RAD daily value is 12.3 on 10 September and 18.4 on 11 September.

## Channels the habitat simulator can replay

| Simulator channel | Real data here | Status |
|---|---|---|
| CO2 | RR-1 (warning case) and RR-5 (near-limit, no warning), 300 s | Time series |
| Temperature, relative humidity | RR-1 and RR-5, 300 s | Time series |
| Radiation dose rate, SPE scenario | 2017 DosTel2, TEPC (uGy and uSv), MSL RAD on Mars | Time series; replaces the team-chosen peak factor 20, duration 20 h and rise 1 h in `station_solar_particle_event` |
| CO, ammonia, VOCs | ANITA-2 monthly averages and mGSC grab samples, Increment 71 | Anchor levels, not a series |
| Noise | Module and sleep station surveys, crew dosimetry | Anchor levels per area, not a series |
| Water quality | Three potable water samples | Anchor values |
| Cabin pressure, ppO2, particulates | None | Not public (see below) |

## Searched for and restricted or unavailable

- **Individual astronaut medical data** (vitals, ECG, sleep, actigraphy, labs, eye exams): released only through LSAH and LSDA after review, with IRB approval, about 13 months for de-identified and about 25 months for attributable data, under the Privacy Act ([request guidelines](https://nlsp.nasa.gov/data-request-guidelines)). Public outputs are pooled. This covers the individual data behind every case above.
- **OSDR human data under controlled access** ([how to request](https://www.nasa.gov/reference/osdr-tutorials-requesting-controlled-access-datasets/)), the **NASA Twins Study** per-subject data and **HERA** analog sleep, actigraphy and performance data: restricted.
- **No vitals or actigraphy in OSDR's open data**, and no astronaut sleep or heart rate dataset on data.nasa.gov. The Inspiration4 blood panels are the only open per-person clinical series found, and they have no in-flight draw and no documented clinical event.
- **Camera twin:** no NASA video with physiological ground truth was found.
- **Cabin pressure and ppO2:** no public ISS time series. **Particulates:** the Airborne Particulate Monitor results are published as summaries only ([NTRS 20220004427](https://ntrs.nasa.gov/citations/20220004427)); no time series.
- **ANITA-2 6 to 10 minute series:** not public; only the monthly means in the increment report.
- **CO2 and headache per report** (Law et al. 2014; Wu 2009 in NASA/TP-2010-216126): odds ratios, counts and plots only. Not digitized.
- **Apollo 15 ECG and in-flight potassium:** printed as events and a figure only.
- **Crew action during the September 2017 event:** no NASA source documents one; the case asserts the watch level only.
- **In-flight kidney stone or decompression sickness with preceding measurements:** not found.

The full list of checked case candidates is in [cases/restricted-and-not-found.json](cases/restricted-and-not-found.json).
