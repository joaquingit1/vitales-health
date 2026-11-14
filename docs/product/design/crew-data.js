/* Crew Health Check prototype: the backend STATE the screens render, as the API would return it.
   It holds no clinical text, limits, frequencies or steps: those come from the configuration documents
   (environment-hazards.json, routine-schedule.json, responder-flows.json) through config-bundle.js,
   and view-model.js joins the two. Synthetic walkthrough data: "sim" values are constructed for the demo,
   "replay" values are NASA data with their original dates. */
window.CREW_DATA = (() => {
  /* the session person and the crew roster (GET /v1/context) */
  const ME = { id: "mara", name: "Mara Okafor" };
  const CREW = [
    { id: "ilya", name: "Ilya Brandt", latest: [
      { code: "pulse", text: "Pulse 64 bpm", small: "07:51, camera station", origin: "sim", region: "heart" },
      { code: "spo2", text: "Blood oxygen 98%", small: "this morning, kit oximeter", origin: "sim", region: "hand" },
      { code: "allergies", text: "Allergies and medicines", small: "from their record", origin: "", region: "", end: "none recorded" },
    ] },
    { id: "sofia", name: "Sofia Reyes", latest: [
      { code: "pulse", text: "Pulse 61 bpm", small: "07:22, camera station", origin: "sim", region: "heart" },
      { code: "allergies", text: "Allergies and medicines", small: "from their record", origin: "", region: "", end: "none recorded" },
    ] },
    { id: "tomas", name: "Tomas Lind", latest: [
      { code: "pulse", text: "Pulse 70 bpm", small: "08:05, camera station", origin: "sim", region: "heart" },
      { code: "allergies", text: "Allergies and medicines", small: "from their record", origin: "", region: "", end: "none recorded" },
    ] },
  ];

  /* the person's own week and latest readings (GET /v1/me/summary) */
  const PERSON = {
    sleep: [6.8, 6.5, 7.0, 6.6, 5.6, 5.4, 5.2],
    mood: [7, 7, 8, 7, 7, 7, 6],
    reaction: [266, 270, 262, 268, 280, 286, 291],
    /* the person's own baselines (Baseline objects) and today's values */
    today: { sleep_min: 312, usual_sleep_min: 408, mood: 6, usual_mood: 7, reaction_ms: 291, usual_reaction_ms: 268, lapses: 4, usual_lapses: 1 },
    /* the assistant's explanation on How you are (an insight with its evidence) */
    why: { text: "Three short nights, and your reaction time is slower. Short sleep slows reactions; it is a pattern, not an illness. One earlier night often brings it back.",
      src: "Draft. Evidence: OCHMO-TB-041 sleep (C15), circadian desynchrony (C23)." },
    vitals: [
      ["meas", "Pulse 58 bpm at rest", "07:37, camera station", "sim", "heart"],
      ["meas", "Breathing 14 per minute", "07:37, camera station", "sim", "lungs"],
      ["meas", "Blood oxygen 97%", "07:44, kit oximeter", "sim", "hand"],
      ["gap", "Blood pressure", "due today, kit cuff", "", "shoulder"],
    ],
    latest: [
      { code: "pulse", text: "Pulse 58 bpm", small: "07:37, camera station", origin: "sim", region: "heart" },
      { code: "spo2", text: "Blood oxygen 97%", small: "07:44, kit oximeter", origin: "sim", region: "hand" },
      { code: "allergies", text: "Allergies and medicines", small: "from your record", origin: "", region: "", end: "none recorded" },
    ],
  };

  /* sensor catalog: which hazard each sensor reports on, its short name, the body region the figure focuses,
     and the gauge range. Display settings only, a team default; limits come from environment-hazards.json. */
  const SENSORS = {
    co2: { hazard: "co2", name: "CO2", long: "Carbon dioxide", region: "head", display: [0, 5] },
    rad: { hazard: "radiation", name: "Radiation", long: "Radiation dose rate", region: null, display: [0, 60] },
    rad_day: { hazard: "radiation", name: "Radiation", long: "Radiation dose", region: null, display: [0, 1] },
    o2: { hazard: "oxygen", name: "Oxygen", long: "Oxygen you breathe in", region: "head", display: [100, 200] },
    noise: { hazard: "noise", name: "Noise", long: "Noise", region: "ear", display: [40, 90] },
    air: { hazard: "toxic_vocs", name: "Air", long: "Air contaminants", region: "lungs", display: [0, 1.5] },
    dust: { hazard: "dust", name: "Dust", long: "Dust in the air", region: "lungs", display: [0, 0.6], display_by_env: { mars_surface: [0, 0.15] } },
    water: { hazard: "perchlorate", name: "Water", long: "Local water", region: "neck", display: [0, 1] },
  };

  const W = (d) => d.map((x, i) => ({ day: 13 + i, done: x[0], due: x[1] }));

  /* per environment of the demo: mission context, latest readings with the backend's evaluation,
     active warnings, routine task instances and the week.
     limit = [index into the hazard's levels in environment-hazards.json, "low" when it is a lower limit];
     trigger = index into the routine item's raise_frequency_when in routine-schedule.json */
  const ENVS = {
    station_leo: {
      day: "flight day 19 of 30", place: "space station",
      ground: { short: "Ground: near real time", delay: "moments", up: true, status: "up" },
      eva: "spacewalk",
      headline: "Short sleep, CO2 above the limit",
      insight: "Three short nights and slower reactions. The habitat CO2 channel, a replayed ISS week, is also over its limit.",
      readings: [
        { sensor: "co2", v: "3.5", u: "mmHg", value: 3.5, level: "act", origin: "replay", srcText: "Replay, ISS 2014", context: "one-hour average", limit: [2], spark: "co2",
          provenance: "Trace: OSDR Rodent Research-1 cabin CO2, 7 Oct 2014, converted at an assumed 760 mmHg." },
        { sensor: "rad", v: "6.1", u: "µGy/h", value: 6.1, level: "ok", origin: "replay", srcText: "Replay, ISS 2022", context: "two-hour mean, Columbus module", spark: "rad",
          provenance: "Trace: OSDR RadLab, DosTel1, 1 to 2 Apr 2022. An instrument's dose rate, not a person's dose." },
        { sensor: "o2", v: "149", u: "mmHg", value: 149, level: "ok", origin: "sim", srcText: "Sim", context: "inspired oxygen", limit: [3, "low"] },
        { sensor: "noise", v: "61", u: "dBA", value: 61, level: "ok", origin: "sim", srcText: "Sim", context: "24-hour average", limit: [2] },
        { sensor: "air", v: "0.3", u: "index", value: 0.3, level: "ok", origin: "sim", srcText: "Sim", context: "toxicity index", limit: [0] },
      ],
      alerts: [
        { id: "co2", hazard: "co2", sensor: "co2", level: "act", by: "sensor_limit", title: "CO2 above the habitat limit", when: "15:36, replay time", region: "head",
          what: "The one-hour average CO2 is 3.5 mmHg. The habitat limit is 3 mmHg.",
          why: "High CO2 can cause headaches, shortness of breath and tiredness, and can disturb sleep. It is a limit for the cabin air, not a diagnosis.",
          src: "CO2 sensor, Replay of ISS cabin data, 7 Oct 2014." },
        { id: "sleep", hazard: null, level: "watch", by: "assistant", title: "Short nights, slower reactions", when: "07:40", region: "eyes",
          what: "Three nights under six hours. Reaction time 291 ms with 4 lapses; your usual is 268 ms with 1.",
          why: "Short sleep and a drifting body clock slow reactions and cause lapses. That matters most for careful tasks such as robotics or a spacewalk.",
          steps: ["Protect your next sleep period, and avoid blue light before it.", "Tell the crew if you have a demanding task today."],
          src: "The assistant, from your sleep (band, Sim) and reaction test (07:38, Sim). A pattern, not a diagnosis. Evidence: OCHMO-MTB-009 Circadian Desynchrony (C23)." },
      ],
      tasks: [
        { item: "daily_check", list: "today", state: "due", end: "due now" },
        { item: "blood_pressure", list: "today", state: "due", end: "due today" },
        { item: "vision_self_check", list: "today", state: "later", end: "13:00", trigger: 1, by: "sensor_limit" },
        { item: "reaction_test", list: "today", state: "done", end: "done 07:38", trigger: 0, by: "rule" },
        { item: "exercise_log", list: "today", state: "later", end: "15:00" },
        { item: "body_mass", list: "week", state: "notdue", end: "day 21" },
        { item: "behavioral_self_check", list: "week", state: "done", end: "done day 17" },
        { item: "cognitive_test", list: "month", state: "notdue", end: "day 28" },
        { item: "hearing_check", list: "month", state: "done", end: "done day 14" },
        { item: "emergency_drill", list: "month", state: "notdue", end: "day 26" },
        { item: "pre_eva_check", list: "walk", state: "notdue", end: "no walk planned" },
        { item: "post_eva_check", list: "walk", state: "notdue", end: "no walk planned" },
      ],
      changes: [{ item: "daily_check", trigger: 1, by: "sensor_limit" }],
      week: W([[4, 4], [3, 4], [5, 5], [4, 4], [3, 4], [4, 5], [1, 5]]),
    },
    transit: {
      day: "mission day 96", place: "deep-space transit",
      ground: { short: "Ground: minutes each way, growing", delay: "several minutes", up: true, status: "delayed" },
      eva: "spacewalk",
      headline: "Solar storm: go to the shelter",
      insight: "Your dose follows the storm warning. Plan to sleep in the shelter.",
      readings: [
        { sensor: "rad", v: "48", u: "µGy/h", value: 48, level: "act", origin: "sim", srcText: "Sim", context: "rising fast, storm warning" },
        { sensor: "co2", v: "2.4", u: "mmHg", value: 2.4, level: "ok", origin: "sim", srcText: "Sim", context: "one-hour average", limit: [2] },
        { sensor: "o2", v: "150", u: "mmHg", value: 150, level: "ok", origin: "sim", srcText: "Sim", context: "inspired oxygen", limit: [3, "low"] },
        { sensor: "noise", v: "57", u: "dBA", value: 57, level: "ok", origin: "sim", srcText: "Sim", context: "24-hour average", limit: [2] },
      ],
      alerts: [
        { id: "storm", hazard: "radiation", sensor: "rad", level: "act", by: "sensor_limit", title: "Solar storm: go to the shelter", when: "08:52", region: null,
          what: "The radiation monitor shows a sharp rise, and the ground warns of a solar particle event.",
          why: "A large solar event can give a big dose in hours. Shielding stops most solar particles, and the shelter has the most.",
          src: "Radiation monitor, Sim, and the ground's warning. Alert dose rates are set by the program." },
      ],
      tasks: [
        { item: "daily_check", list: "today", state: "due", end: "due now" },
        { item: "radiation_dosimeter", list: "today", state: "due", end: "now, in the shelter", trigger: 0, by: "sensor_limit" },
        { item: "exercise_log", list: "today", state: "later", end: "after the all clear" },
        { item: "blood_pressure", list: "week", state: "notdue", end: "day 98" },
        { item: "body_mass", list: "week", state: "notdue", end: "day 100" },
        { item: "behavioral_self_check", list: "week", state: "notdue", end: "day 99" },
        { item: "hearing_check", list: "month", state: "notdue", end: "day 104" },
        { item: "cognitive_test", list: "month", state: "notdue", end: "day 110" },
        { item: "emergency_drill", list: "month", state: "done", end: "done day 90" },
        { item: "pre_eva_check", list: "walk", state: "notdue", end: "no walk planned" },
        { item: "post_eva_check", list: "walk", state: "notdue", end: "no walk planned" },
      ],
      changes: [{ item: "daily_check", trigger: 2, by: "sensor_limit" }],
      week: W([[4, 4], [4, 4], [3, 4], [4, 4], [4, 5], [3, 4], [0, 3]]),
    },
    lunar_surface: {
      day: "surface day 4", place: "Moon base",
      ground: { short: "Ground: 6 to 10 seconds", delay: "6 to 10 seconds", up: true, status: "up" },
      eva: "surface walk",
      headline: "Dust after the surface walk",
      insight: "Dust is rising after the walk. Brush suits off in the airlock.",
      readings: [
        { sensor: "dust", v: "0.42", u: "mg/m³", value: 0.42, level: "act", origin: "sim", srcText: "Sim", context: "time-weighted, airlock", limit: [2] },
        { sensor: "rad", v: "13", u: "µGy/h", value: 13, level: "ok", origin: "sim", srcText: "Sim", context: "surface, now" },
        { sensor: "co2", v: "2.1", u: "mmHg", value: 2.1, level: "ok", origin: "sim", srcText: "Sim", context: "one-hour average", limit: [2] },
        { sensor: "o2", v: "148", u: "mmHg", value: 148, level: "ok", origin: "sim", srcText: "Sim", context: "inspired oxygen", limit: [3, "low"] },
      ],
      alerts: [
        { id: "dust", hazard: "dust", sensor: "dust", level: "act", by: "sensor_limit", title: "Dust rising after the surface walk", when: "11:55", region: "lungs",
          what: "Dust in the airlock is rising after the walk: the time-weighted average is 0.42 mg/m³. The lunar limit is 0.3 mg/m³.",
          why: "Lunar dust is fine and sharp. It irritates the eyes and skin and can harm the lungs.",
          src: "Airlock dust sensor, Sim." },
      ],
      /* Mara was not on the walk: no post-walk check for her; the dust check applies to everyone in the area */
      tasks: [
        { item: "dust_exposure_check", list: "today", state: "due", end: "due now", trigger: 0, by: "sensor_limit" },
        { item: "gravity_arrival_check", list: "today", state: "due", end: "due today" },
        { item: "daily_check", list: "today", state: "done", end: "done 07:40" },
        { item: "exercise_log", list: "today", state: "later", end: "16:00" },
        { item: "blood_pressure", list: "week", state: "notdue", end: "day 6" },
        { item: "body_mass", list: "week", state: "notdue", end: "day 7" },
        { item: "hearing_check", list: "month", state: "notdue", end: "day 18" },
        { item: "cognitive_test", list: "month", state: "notdue", end: "day 20" },
        { item: "pre_eva_check", list: "walk", state: "notdue", end: "next walk day 6" },
        { item: "post_eva_check", list: "walk", state: "notdue", end: "next walk day 6" },
      ],
      changes: [],
      week: W([[4, 4], [3, 3], [4, 4], [4, 4], [5, 5], [4, 4], [1, 5]]),
      /* the same base earlier, while surface walk 1 is under way: no dust yet, the walkers outside */
      phases: {
        walk: {
          headline: "Surface walk 1 under way",
          insight: "Ilya and Sofia are outside until 11:40. Everything here runs on board.",
          walk: { name: "surface walk 1", outside: ["ilya", "sofia"], until: "11:40" },
          readings: [
            { sensor: "dust", v: "0.05", u: "mg/m³", value: 0.05, level: "ok", origin: "sim", srcText: "Sim", context: "time-weighted, airlock", limit: [2] },
            { sensor: "rad", v: "13", u: "µGy/h", value: 13, level: "ok", origin: "sim", srcText: "Sim", context: "surface, now" },
            { sensor: "co2", v: "2.1", u: "mmHg", value: 2.1, level: "ok", origin: "sim", srcText: "Sim", context: "one-hour average", limit: [2] },
            { sensor: "o2", v: "148", u: "mmHg", value: 148, level: "ok", origin: "sim", srcText: "Sim", context: "inspired oxygen", limit: [3, "low"] },
          ],
          alerts: [],
          tasks: [
            { item: "gravity_arrival_check", list: "today", state: "due", end: "due today" },
            { item: "daily_check", list: "today", state: "done", end: "done 07:40" },
            { item: "exercise_log", list: "today", state: "later", end: "16:00" },
            { item: "blood_pressure", list: "week", state: "notdue", end: "day 6" },
            { item: "body_mass", list: "week", state: "notdue", end: "day 7" },
            { item: "hearing_check", list: "month", state: "notdue", end: "day 18" },
            { item: "cognitive_test", list: "month", state: "notdue", end: "day 20" },
            { item: "post_eva_check", list: "walk", state: "notdue", end: "after walk 1, for Ilya and Sofia" },
          ],
          changes: [],
        },
      },
    },
    mars_surface: {
      day: "sol 3 on Mars", place: "Mars base",
      ground: { short: "Ground: up to 20 min each way", delay: "up to 20 minutes", up: true, status: "delayed" },
      eva: "surface walk",
      headline: "Sol 3: getting used to gravity",
      insight: "Dizzy when standing is common after landing. Stand up slowly.",
      readings: [
        { sensor: "rad_day", v: "0.5", u: "mSv/day", value: 0.5, level: "ok", origin: "sim", srcText: "Sim", context: "NASA's surface planning value" },
        { sensor: "dust", v: "0.04", u: "mg/m³", value: 0.04, level: "ok", origin: "sim", srcText: "Sim", context: "time-weighted, habitat", limit: [3] },
        { sensor: "co2", v: "2.7", u: "mmHg", value: 2.7, level: "ok", origin: "sim", srcText: "Sim", context: "one-hour average", limit: [2] },
        { sensor: "water", v: "Untested", u: "", value: null, level: "watch", origin: "sim", srcText: "Sim", context: "from local ice" },
      ],
      alerts: [
        { id: "dizzy", hazard: "partial_gravity", level: "watch", by: "rule", title: "Dizzy when standing", when: "yesterday 18:20", region: "heart",
          what: "You reported dizziness after standing up, on your second sol in gravity.",
          why: "After months weightless, the body needs time to keep blood pressure up when you stand.",
          src: "Your report yesterday. Rule: dizziness when standing after landing." },
        { id: "water", hazard: "perchlorate", sensor: "water", level: "watch", by: "rule", title: "Local water not yet tested", when: "06:00", region: "neck",
          what: "The new water from local ice has not been tested for perchlorate.",
          why: "Perchlorate can stop the thyroid taking up iodine. Martian dust and soil carry it.",
          src: "Rule: local water is tested before first use." },
      ],
      tasks: [
        { item: "gravity_arrival_check", list: "today", state: "due", end: "due now" },
        { item: "blood_pressure", list: "today", state: "due", end: "due now", trigger: 2, by: "rule" },
        { item: "daily_check", list: "today", state: "due", end: "due now" },
        { item: "exercise_log", list: "today", state: "later", end: "15:00" },
        { item: "body_mass", list: "week", state: "notdue", end: "sol 7" },
        { item: "behavioral_self_check", list: "week", state: "notdue", end: "sol 5" },
        { item: "hearing_check", list: "month", state: "notdue", end: "sol 20" },
        { item: "cognitive_test", list: "month", state: "notdue", end: "sol 25" },
        { item: "pre_eva_check", list: "walk", state: "notdue", end: "first walk sol 8" },
        { item: "post_eva_check", list: "walk", state: "notdue", end: "first walk sol 8" },
      ],
      week: W([[3, 3], [3, 3], [2, 2], [1, 1], [2, 3], [3, 4], [0, 4]]),
    },
  };

  /* demo entry points into Someone needs help: help sessions as the backend would hold them */
  const HELP_PRESETS = {
    eye: { env: "lunar_surface", who: "tomas", node: "eye_injury", log: ["Air safe", "Tomas Lind responding", "No heavy bleeding", "Eye injury, chemical or dust in the eye"] },
    bleed: { who: "ilya", node: "severe_bleeding", where: "hand", log: ["Air safe", "Ilya Brandt responding", "Heavy bleeding"] },
    cpr: { who: "ilya", node: "not_breathing", log: ["Air safe", "Ilya Brandt not responding", "Not breathing normally"] },
  };
  /* a red flag answered yes in a concern opens this help flow (team configuration) */
  const REDFLAG_FLOW = { chest: "chest_pain", faint: "fainting", breath: "breathing_difficulty" };
  /* the walkthrough: act 6 moves to this place, then starts help there */
  const DEMO = { home: "station_leo", new_place: "lunar_surface", help_preset: "eye", walk_phase: "walk",
    /* the mission clock at the start of each act (fixture demo-mara-fd19, MET day 018): the walk ends at 11:40, the dust warning is at 11:55 */
    act_met: { 1: "018/09:10:16", 2: "018/09:10:16", 3: "018/09:20:40", 4: "018/09:30:00", 5: "018/09:34:30", 6: "018/12:02:00" } };

  /* crew roles (GET /v1/crew) and trained hands (SkillRecords, Sim: preflight training and drills) */
  const ROLES = { ilya: "crew_medical_officer", sofia: "second_medical_officer", mara: "crew", tomas: "crew" };
  const SKILL_RECORDS = [
    { person: "tomas", skill: "pulse_check_wrist", last_practiced: "flight day 12", source: "drill" },
    { person: "tomas", skill: "cpr", last_practiced: "flight day 12", source: "drill" },
    { person: "mara", skill: "cpr", last_practiced: "flight day 12", source: "drill" },
  ];

  /* the ground link set with the demo switch in act 3 (GroundLink, source demo_switch, Sim) */
  const LINK_DEMO = { status: "down", next_contact_in_h: 26, gap_kind: "planned_line_of_sight", gap_planned: true, source: "demo_switch", origin: "sim" };

  /* the demo concern as the hub holds it (an Episode): the condition whose test is not aboard opens its plan */
  const EPISODE = {
    title: "Racing heart",
    condition_id: "ICL10",
    observed: [
      { mk: "rep", text: "Racing heart since this morning", small: "reported 09:30, comes and goes, easing", origin: "", region: "heart" },
      { mk: "none", text: "No chest pain, fainting or hard breathing", small: "answered 09:30", origin: "", region: "" },
      { mk: "meas", text: "Pulse 96 bpm at rest", small: "09:33, cameras 2 and 3 agree", origin: "live", region: "heart", after: "measured" },
      { mk: "none", text: "One failed reading", small: "09:32, too much movement, kept", origin: "live", region: "head", after: "failed_try" },
      { mk: "meas", text: "Sleep 5 h 12 min", small: "band estimate, confirmed", origin: "sim", region: "wrist" },
      { mk: "meas", text: "Stress 5 of 10", small: "self rated, 07:42", origin: "sim", region: "head" },
    ],
    explain: [
      { text: "Short sleep last night", small: "2 records, 1 passage, NASA list ICL18" },
      { text: "Stress", small: "1 record, 1 passage, NASA list ICL14" },
      { text: "A heart rhythm problem", small: "possible, not excluded: this kit can't test it, NASA list ICL10", region: "heart" },
    ],
    /* the cameras' pulse while a crewmate counts (the reference for the guided check) */
    camera_reference: { value: 96, unit: "bpm", source: "cameras 2 and 3", origin: "live" },
    /* when the concern was raised and the camera tries were taken (mission time) */
    times: { reported: "09:30", failed: "09:32", measured: "09:33" },
    /* the crewmate's count when the walkthrough runs without anyone tapping (live, a teammate taps Beat) */
    demo_check: { skill_id: "pulse_check_wrist", helper: "tomas", beats: 92, seconds: 60, answer: "regular", origin: "sim" },
  };

  /* the assistant's measurement request in the demo concern: model output stored with the episode.
     The server's kit check runs on capability_code (and on unmet_code, which the kit cannot meet). */
  const REQUEST = { capability_code: "pulse", unmet_code: "ecg_rhythm",
    text: "I'd like your pulse at rest. It shows how fast your heart beats now. It can't show the rhythm." };

  /* the person's records today (GET /v1/me/observations). "after" names the walkthrough step that creates the row */
  const RECORDS = [
    { mk: "meas", text: "Pulse 96 bpm at rest", small: "09:33, cameras 2 and 3, good", origin: "live", region: "heart", after: "measured" },
    { mk: "none", text: "Pulse, no reading", small: "09:32, too much movement, kept", origin: "live", region: "head", after: "failed_try" },
    { mk: "rep", text: "Racing heart", small: "09:30, reported by Mara", origin: "", region: "heart" },
    { mk: "meas", text: "Blood oxygen 97%", small: "07:44, kit oximeter", origin: "sim", region: "hand" },
    { mk: "meas", text: "Reaction time 291 ms", small: "07:38, 4 lapses", origin: "sim", region: "eyes" },
    { mk: "meas", text: "Sleep 5 h 12 min", small: "07:02, band estimate, confirmed", origin: "sim", region: "wrist" },
    { mk: "due", text: "Blood pressure", small: "due today, kit cuff", origin: "", region: "shoulder" },
    { mk: "none", text: "Cabin CO2 3.5 mmHg, one hour", small: "ISS, 7 Oct 2014, not Mara's exposure", origin: "replay", region: "" },
  ];

  /* where the person's own values come from (the hub's provenance for each stream); habitat sensors carry their own */
  const PROVENANCE = [
    { what: "Pulse and breathing", from: "Camera station", origin: "live", note: "the presenter's own readings, kept in a separate demo-operator record" },
    { what: "Blood oxygen", from: "Kit oximeter", origin: "sim", note: "constructed for the walkthrough" },
    { what: "Sleep", from: "Wrist band", origin: "sim", note: "band estimate, confirmed by the person" },
    { what: "Mood and stress", from: "Self ratings", origin: "sim", note: "constructed for the walkthrough" },
    { what: "Reaction time", from: "Reaction test", origin: "sim", note: "constructed for the walkthrough" },
  ];

  /* the result panel (GET /v1/results). value null means not measured yet: the numbers are produced at the event */
  const RESULTS = [
    { id: "camera_study", name: "Camera pulse against the kit oximeter, at rest", detail: "people, windows, mean error, bias, refusal rate", value: null },
    { id: "camera_agree", name: "One camera against two agreeing", detail: "error and refusals for each", value: null },
    { id: "breathing", name: "Breathing rate against counted breaths", detail: "reported separately from pulse", value: null },
    { id: "turn_time", name: "Assistant turn time on the M3 laptop", detail: "median and slowest, with failures counted", value: null },
    { id: "rules_vs_ai", name: "Rules alone against rules plus the assistant", detail: "on the frozen scenarios, failures counted", value: null },
    { id: "crewmate_count", name: "A crewmate's pulse count against the cameras", detail: "pairs, mean difference, disagreements kept", value: null },
    { id: "compression_rate", name: "Compression rate from the cameras on a manikin", detail: "error against the manikin, can't-see rate", value: null },
    { id: "scenarios", name: "Scenarios passing", detail: "V01 to V98", value: null },
    { id: "demo_assertions", name: "Demo assertions passing", detail: "D01 to D44", value: null },
  ];

  return { ME, CREW, PERSON, SENSORS, ENVS, HELP_PRESETS, REDFLAG_FLOW, DEMO, REQUEST, RECORDS, PROVENANCE, RESULTS, ROLES, SKILL_RECORDS, LINK_DEMO, EPISODE };
})();
