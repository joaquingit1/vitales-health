/* Crew Health Check prototype: the client view model.
   Joins the configuration documents (window.CREW_CONFIG, built from environment-hazards.json,
   routine-schedule.json and responder-flows.json) with backend state (window.CREW_DATA) into what the
   screens draw. Every hazard effect, limit, step, routine frequency and help step on screen comes from a
   configuration document through this file; editing a document and rebuilding the bundle changes the
   screens with no change here. The only words defined here are display words for the fixed vocabulary. */
window.CREW_VIEW = (() => {
  const C = window.CREW_CONFIG, D = window.CREW_DATA;
  const EH = C["environment-hazards.json"].document;
  const RS = C["routine-schedule.json"].document;
  const RF = C["responder-flows.json"].document;
  const by = (arr, k = "id") => Object.fromEntries(arr.map((x) => [x[k], x]));
  const HZ = by(EH.hazards), ENVC = by(EH.environments), ITEM = by(RS.items), FLOW = by(RF.flows), TRIAGE = by(RF.triage);
  const SRC = {};
  [EH, RS, RF].forEach((doc) => doc.sources.forEach((s) => { if (!SRC[s.id]) SRC[s.id] = s; }));

  /* display words for the fixed vocabulary (UX-DESIGN.md section 1.13) */
  const LEVEL = { info: "For your information", watch: "Keep an eye on it", act: "Act now", emergency: "Emergency" };
  const BY = { rule: "Raised by a rule", sensor_limit: "Raised by a sensor limit", assistant: "Raised by the assistant" };

  /* text helpers: source IDs in parentheses are cited separately, never read out as steps */
  const REF = /\s*\((?:[A-Z]{1,2}\d{2}\b[^()]*)\)/g;
  const refsIn = (t) => [...String(t || "").matchAll(/\b([A-Z]{1,2}\d{2})\b/g)].map((m) => m[1]);
  const clean = (t) => String(t || "").replace(REF, "").replace(/\s+/g, " ").trim();
  const sentences = (t) => clean(t).split(/(?<=[.!?])\s+(?=[A-Z"])/).filter(Boolean);
  const uniq = (a) => [...new Set(a.filter(Boolean))];
  const unit = (u) => (!u || /unitless/.test(u) ? "" : u.replace(/\bm3\b/, "m³"));
  const shortTitle = (id) => (SRC[id] ? SRC[id].title.split(": ")[0].replace(/\s*\([^()]*(technical brief|package ID)[^()]*\)/g, "") : id);
  const cites = (ids) => uniq(ids).map((id) => shortTitle(id) + " (" + id + ")").join("; ");
  const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t);
  const plainName = (n) => n.replace(/\s*\([^()]*\)\s*$/, "");

  /* ---------- environments, readings, places and warnings ---------- */
  function sensorView(envId, r) {
    const cat = D.SENSORS[r.sensor], h = HZ[cat.hazard];
    const row = r.limit ? h.levels[r.limit[0]] : null;
    const [mn, mx] = (cat.display_by_env && cat.display_by_env[envId]) || cat.display;
    const pos = (x) => Math.max(0, Math.min(1, (x - mn) / (mx - mn)));
    const hasLimit = row && row.value != null;
    const limitText = hasLimit ? (r.limit[1] === "low" ? "low limit " : "limit ") + row.value + (unit(row.unit) ? " " + unit(row.unit) : "") : "";
    const lvl = r.level === "ok" ? "info" : r.level;
    const here = h.by_environment && h.by_environment[envId];
    return {
      id: r.sensor, hazard: h.id, name: cat.name, long: cat.long, v: r.v, u: r.u,
      frac: r.value == null ? 0 : pos(r.value), lim: hasLimit ? pos(row.value) : null,
      level: r.level, src: r.origin, srcText: r.srcText, region: cat.region, spark: r.spark,
      state: cap([limitText, r.context].filter(Boolean).join(", ")),
      effects: h.effects.map((e) => clean(e.effect)),
      todo: sentences(h.what_to_do[lvl]),
      here: here ? clean(here) : "",
      cite: (hasLimit ? "Limit: " + cites(row.source_ids) + ". " : "") + "What it can do: " + cites(h.effects.flatMap((e) => e.source_ids)) + "." + (refsIn(h.what_to_do[lvl]).length ? " What to do: " + cites(refsIn(h.what_to_do[lvl])) + "." : "") + (r.provenance ? " " + r.provenance : ""),
    };
  }
  function placeView(id) {
    const h = HZ[id];
    return {
      id, name: plainName(h.name),
      regions: uniq(h.effects.map((e) => e.body_region)),
      effects: h.effects.map((e) => clean(e.effect)),
      todo: uniq(sentences(h.what_to_do.info + " " + h.what_to_do.watch)).filter((s) => !/^Nothing to do/.test(s)),
    };
  }
  function alertView(a) {
    const h = a.hazard && HZ[a.hazard];
    const text = h ? h.what_to_do[a.level] : "";
    return Object.assign({}, a, {
      steps: h ? sentences(text) : a.steps,
      src: a.src + (h ? " What to do: " + (refsIn(text).length ? cites(refsIn(text)) : plainName(h.name) + " in environment-hazards.json") + "." : ""),
    });
  }

  /* ---------- routine ---------- */
  const startsDet = (e) => /^(a|an|the|each|every|any|flight day|landing|\d)/i.test(e);
  function cadWords(c, eva) {
    if (!c) return "";
    switch (c.word) {
      case "daily": return "daily";
      case "weekly": return "weekly";
      case "monthly": return "monthly";
      case "every_n_days": return "every " + c.n + " days";
      case "per_eva": return "per " + eva;
      case "after_event": return c.event ? (/^flight day/i.test(c.event) ? "at " : "after ") + (startsDet(c.event) ? c.event : "the " + c.event) : "after an event";
      case "continuous": return "all the time";
      case "as_needed": return "when needed";
      case "mission_phase": return c.phase || "by mission phase";
      default: return c.word;
    }
  }
  function trig(envId, row) {
    if (!row || row.trigger == null) return null;
    return ITEM[row.item].raise_frequency_when[row.trigger];
  }
  function task(id, envId, phase) {
    const it = ITEM[id]; if (!it) return null;
    const at = D.ENVS[envId], env = phase && at.phases && at.phases[phase] ? Object.assign({}, at, at.phases[phase]) : at;
    const row = env.tasks.find((t) => t.item === id);
    const base = it.cadence.by_environment[envId] || it.cadence.default;
    const t = trig(envId, row);
    const basis = t ? t.basis : it.cadence.basis;
    return {
      id, name: it.name.split(/: | \(/)[0], full: it.name,
      cad: (t ? t.new_cadence : base).word,
      cadText: cadWords(t ? t.new_cadence : base, env.eva),
      min: it.minutes, kit: it.instrument.split(";")[0], region: it.body_region,
      what: it.plain, steps: it.steps, why: it.why,
      src: "Frequency: " + (basis === "sourced" ? "from the sources" : "team default, to be set by the flight surgeon") + (t ? ", changed because " + clean(t.trigger).replace(/\s*\((sensor_limit|rule|assistant)\)/, "").toLowerCase() : "") + ". Sources: " + (cites(it.source_ids) || "none") + ".",
    };
  }
  function changedView(envId, env = D.ENVS[envId]) {
    const rows = env.tasks.filter((t) => t.trigger != null).concat(env.changes || []);
    return rows.map((r) => {
      const it = ITEM[r.item], t = it.raise_frequency_when[r.trigger], c = t.new_cadence;
      return [it.name.split(/: | \(/)[0], cap(c.note || cadWords(c, env.eva)), cap(clean(t.trigger).replace(/\s*\((sensor_limit|rule|assistant)\)/, "")) + ". " + BY[r.by]];
    });
  }
  const eventItems = (envId) => RS.items.filter((it) => (it.cadence.by_environment[envId] || it.cadence.default).word === "after_event").map((it) => it.id);

  /* ---------- the four environments of the demo ---------- */
  const ENVS = {};
  function envView(id, s) {
    const c = ENVC[id], short = c.name.split(",")[0];
    return {
      label: short, name: short, day: s.day, place: s.place, eva: s.eva,
      sub: sentences(c.what_is_different).slice(0, 2).join(" "),
      gravity: clean(c.gravity),
      ground: s.ground, walk: "Per " + s.eva, headline: s.headline, insight: s.insight, walkNow: s.walk || null,
      sensors: s.readings.map((r) => sensorView(id, r)),
      places: c.hazard_ids.filter((h) => HZ[h].kind === "condition").map(placeView),
      alerts: s.alerts.map(alertView),
      routine: s.tasks.map((t) => [t.item, t.list, t.state, t.end]),
      changed: changedView(id, s),
      week: s.week,
    };
  }
  Object.keys(D.ENVS).forEach((id) => {
    const s = D.ENVS[id];
    ENVS[id] = envView(id, s);
    /* a phase is the same place at another moment (for example during a surface walk): its fields override */
    ENVS[id].phases = Object.fromEntries(Object.entries(s.phases || {}).map(([k, p]) => [k, envView(id, Object.assign({}, s, p))]));
  });

  /* ---------- Someone needs help: the fixed first screen and the flows ---------- */
  const HELP = {
    triage: TRIAGE, flows: FLOW, first: RF.triage[0].id,
    question: (n) => { const q = TRIAGE[n].question; const i = q.indexOf("? "); return i > 0 ? [q.slice(0, i + 1), q.slice(i + 2)] : [q, ""]; },
    flow: (id) => {
      const f = FLOW[id]; if (!f) return null;
      return Object.assign({}, f, {
        steps: f.immediate_steps.map(clean),
        region: (f.body_regions || [])[0] || null,
        cite: cites(f.source_ids),
      });
    },
    note: (id, envId) => { const f = FLOW[id]; return f && f.environment_notes && f.environment_notes[envId] ? clean(f.environment_notes[envId]) : ""; },
  };

  /* ---------- the kit map: what this ship can test ----------
     In the product the server computes these states (the kit map view and its what-if) and the client
     only draws them. The prototype has no server, so it applies the contract's precedence rule to the
     bundled document here: a test whose instruments are all aboard, else a test that needs no
     instrument, else the test with the smallest missing set. */
  const KM = C["kit-map.json"] ? C["kit-map.json"].document : null;
  const KIT = KM ? (() => {
    const INS = by(KM.instruments);
    const STATES = ["measured_here", "report_only", "needs_test_not_aboard"];
    const name = (i) => (INS[i] ? INS[i].needs_text || INS[i].name : i);
    function view(envId, add = []) {
      const aboard = new Set((KM.kits[envId] || []).concat(add));
      const rows = KM.conditions.map((c) => {
        const tests = c.confirming_tests;
        const full = tests.find((t) => t.instruments.length && t.instruments.every((i) => aboard.has(i)));
        const none = tests.find((t) => !t.instruments.length);
        let state = full ? "measured_here" : none ? "report_only" : "needs_test_not_aboard", test = full || none, missing = [];
        if (!test) {
          tests.forEach((t) => { const m = t.instruments.filter((i) => !aboard.has(i)); if (!test || m.length < missing.length) { test = t; missing = m; } });
        }
        return { id: c.condition_id, name: c.name, state, test: test ? test.name : "", missing: missing.map(name), regions: c.body_regions, region: c.body_regions[0], cliff: c.cliff_url, plan_id: c.plan_id || null, ground_needs: c.ground_needs || null };
      });
      const counts = Object.fromEntries(STATES.map((s) => [s, rows.filter((r) => r.state === s).length]));
      /* one mark per condition at its first region: a dot or a hollow ring, and a dashed ring for a missing test */
      const marks = {};
      rows.forEach((r) => {
        const m = marks[r.region] || (marks[r.region] = { dot: false, hollow: false, open: false, rows: [] });
        m.rows.push(r);
        if (r.state === "measured_here") m.dot = true; else if (r.state === "report_only") m.hollow = true; else m.open = true;
      });
      return { env: envId, rows, counts, total: rows.length, marks, aboard: [...aboard].filter((i) => INS[i]).map((i) => INS[i]) };
    }
    const closes = (envId, id) => view(envId).counts.needs_test_not_aboard - view(envId, [id]).counts.needs_test_not_aboard;
    const candidates = (envId) => (KM.candidates || []).filter((i) => !(KM.kits[envId] || []).includes(i)).map((i) => INS[i]);
    /* the kit check on a measurement request: the aboard instruments that can meet a capability code,
       or, when none can, what it needs and the forms of the conditions whose tests use it */
    function check(envId, code) {
      const kit = new Set(KM.kits[envId] || []);
      const withCode = KM.instruments.filter((i) => (i.capability_codes || []).includes(code));
      const can = withCode.filter((i) => kit.has(i.id)), ids = new Set(withCode.map((i) => i.id));
      const forms = KM.conditions.filter((c) => c.confirming_tests.some((t) => t.instruments.some((x) => ids.has(x)))).map((c) => ({ id: c.condition_id, name: c.name, cliff: c.cliff_url }));
      const cap = (KM.capabilities || []).find((c) => c.code === code);
      return { code, label: cap ? plainName(cap.label) : code, region: cap ? cap.body_region : null, ok: can.length > 0, can: can.map((i) => i.name), needs: can.length ? [] : withCode.map((i) => i.needs_text || i.name), forms: can.length ? [] : forms };
    }
    const review = { total: KM.conditions.length, unreviewed: KM.conditions.filter((c) => c.review_state === "unreviewed").length, checked: KM.conditions.filter((c) => c.checked_by).length };
    return { example: !!KM.example, notice: KM.notice || "", source: C["kit-map.json"].source || "kit-map.json", view, closes, candidates, check, review, STATES };
  })() : null;

  /* ---------- the plan aboard, trained hands and roles ----------
     In the product the server computes these (the PlanView of GET /v1/conditions/{id}/plan, SkillRecord
     freshness, role proposals); the prototype applies the same rules to the bundled documents
     (onboard-plans.json, skills-catalog.json, role-rules.json) and the roster. Nothing in a plan waits for
     the ground: its ground stage is last and never blocks. */
  const doc = (n) => (C[n] ? C[n].document : null);
  const OP = doc("onboard-plans.json"), SK = doc("skills-catalog.json"), RR = doc("role-rules.json");
  const SKILL = SK ? by(SK.skills, "skill_id") : {};
  const LANDMARK = SK && SK.landmarks ? by(SK.landmarks, "landmark_id") : {};
  const person = (id) => (id === D.ME.id ? D.ME : D.CREW.find((c) => c.id === id) || { id, name: id });
  const moment = (envId, phase) => { const e = D.ENVS[envId]; return phase && e.phases && e.phases[phase] ? Object.assign({}, e, e.phases[phase]) : e; };
  /* the crew at this moment, apart from the subject: role, where they are, what they have practiced */
  function crewNow(envId, phase, subject) {
    const w = moment(envId, phase).walk;
    return [D.ME].concat(D.CREW).filter((p) => p.id !== subject).map((p) => ({
      id: p.id, name: p.name, role: D.ROLES[p.id] || "crew",
      outside: !!(w && w.outside.includes(p.id)), until: w ? w.until : null, walk: w ? w.name : null,
      skills: D.SKILL_RECORDS.filter((r) => r.person === p.id),
    }));
  }
  const isOfficer = (p) => /medical_officer$/.test(p.role);
  /* who can do a step now: never the subject, never someone outside for a step done here */
  function whoCan(who, here) {
    const inside = here.filter((p) => !p.outside);
    if (!who || who === "any_crew") return inside;
    if (who === "crew_medical_officer") return inside.filter(isOfficer);
    const skill = who.replace(/^skill:/, "");
    return inside.filter((p) => p.skills.some((k) => k.skill === skill));
  }
  function planOf(conditionId) {
    if (!OP) return null;
    const row = KM && KM.conditions.find((c) => c.condition_id === conditionId);
    if (row && row.plan_id === null) return null;
    return OP.plans.find((x) => (row && row.plan_id ? x.plan_id === row.plan_id : (x.scope || {}).condition_id === conditionId)) || null;
  }
  /* the PlanView: each step's availability with this kit, these supplies and the crew inside now */
  function plan(conditionId, envId, phase, subject) {
    const p = planOf(conditionId); if (!p || (p.environments && !p.environments.includes(envId))) return null;
    const kit = new Set((KM && KM.kits[envId]) || []);
    const stock = KM && KM.stock ? Object.fromEntries((KM.stock[envId] || []).map((s) => [s.supply_id, s.quantity])) : null;
    const here = crewNow(envId, phase, subject);
    const supplyName = (id) => ((KM && KM.supplies) ? (KM.supplies.find((s) => s.supply_id === id) || {}).name || id : id);
    const insName = (id) => { const i = KM && KM.instruments.find((x) => x.id === id); return i ? i.needs_text || i.name : id; };
    const forEnv = (s) => !s.environments || s.environments.includes(envId);
    /* a supply is short when it is not packed here, or when its packed quantity is known and below one use */
    const short = (u) => stock && (!(u.supply_id in stock) || (stock[u.supply_id] != null && u.per_use != null && stock[u.supply_id] < u.per_use));
    const step = (s) => {
      const n = s.needs || {};
      const missing = (n.instruments || []).filter((i) => !kit.has(i)).map(insName).concat((n.supplies || []).filter(short).map((u) => supplyName(u.supply_id)));
      const can = whoCan(s.who, here);
      const availability = missing.length ? (s.substitute_step_id ? "substituted" : "not_aboard") : can.length ? "aboard" : "needs_skill_holder";
      return Object.assign({}, s, { text: clean(s.text), availability, missing, who_can: can.map((x) => x.id),
        skill: s.guided_check_skill_id || (/^skill:/.test(s.who || "") ? s.who.slice(6) : (n.skills || [])[0] || null) });
    };
    /* a substitute-only step is shown only in place of the step it substitutes */
    const all = (p.stages || []).flatMap((st) => (st.steps || []).concat(st.prepare || []));
    const withSub = (s) => { const v = step(s); if (v.availability === "substituted") { const alt = all.find((x) => x.step_id === s.substitute_step_id); v.substitute = alt ? step(alt) : null; if (v.substitute && v.substitute.availability !== "aboard") v.availability = "not_aboard"; } return v; };
    const main = (arr) => arr.filter(forEnv).filter((s) => !s.substitute_only).map(withSub);
    const stages = (p.stages || []).map((st) => {
      if (st.stage_id === "ground_log") return { id: "ground_log", blocking: false, items: st.items || [], steps: [] };
      if (st.stage_id === "return") return !st.environments || st.environments.includes(envId) ? { id: "return", criteria: st.criteria || [], decided_by: st.decided_by || [], rtdc: st.rtdc || null, steps: main(st.prepare || []) } : null;
      return { id: st.stage_id, steps: main(st.steps || []) };
    }).filter(Boolean);
    return { plan_id: p.plan_id, title: p.title, condition_id: conditionId, environment_id: envId, written_for: p.written_for, mode: p.mode, review_state: p.review_state || "unreviewed", stages, here,
      officers: here.filter(isOfficer).sort((a, b) => (a.role === "crew_medical_officer" ? -1 : b.role === "crew_medical_officer" ? 1 : 0)),
      plan_aboard: stages.some((s) => s.id !== "ground_log" && s.steps.some((x) => (!x.who || x.who === "any_crew") && x.availability !== "not_aboard")) };
  }
  /* who does what in a help session: the role engine's proposals (role-rules.json). The medical officer
     follows the cascade (first officer, then second, never the patient, never someone outside); skill roles
     go to a crewmate who has practiced the skill; one role each; every row states its reason and can be changed. */
  function proposeRoles(flowId, envId, phase, subject, first = D.ME.id) {
    if (!RR) return [];
    const slots = (((RR.flow_roles || []).find((f) => f.flow_id === flowId) || {}).roles) || RR.default_roles || [];
    const m = moment(envId, phase), all = crewNow(envId, phase, null), def = by(RR.roles, "role_id"), used = new Set([subject]), out = [];
    const say = (code, v) => String((RR.reason_phrases || {})[code] || code).replace(/\{(\w+)\}/g, (x, k) => (v[k] != null ? v[k] : x));
    const put = (slot, p, code, v) => { if (p) used.add(p.id); out.push({ role_id: slot.role_id, label: (def[slot.role_id] || {}).label || slot.role_id, task: slot.task_text, skill_id: slot.skill_id, person: p, reason_code: code, reason: say(code, v || {}) }); };
    const mo = slots.find((s) => s.role_id === "medical_officer");
    if (mo) {
      const officers = all.filter(isOfficer).sort((a, b) => (a.role === "crew_medical_officer" ? -1 : b.role === "crew_medical_officer" ? 1 : 0));
      let skip = null, pick = null;
      for (const o of officers) {
        if (o.id === subject) { skip = skip || ["officer_is_subject", { officer_name: o.name }]; continue; }
        if (o.outside) { skip = skip || ["officer_on_eva", { officer_name: o.name, eva_word: m.eva, time: o.until }]; continue; }
        pick = o; break;
      }
      if (pick) put(mo, pick, skip ? skip[0] : pick.role === "crew_medical_officer" ? "first_officer" : "second_officer", skip ? skip[1] : {});
    }
    slots.filter((s) => s.role_id !== "medical_officer").forEach((slot) => {
      const onSite = (def[slot.role_id] || {}).on_site;
      const cands = all.filter((p) => !used.has(p.id) && !(onSite && p.outside));
      const skilled = slot.skill_id ? cands.filter((p) => p.skills.some((k) => k.skill === slot.skill_id)) : [];
      const p = skilled[0] || cands.find((x) => x.id === first) || cands[0];
      if (!p) { if (slot.required) put(slot, null, "free_to_help", {}); return; }
      const rec = skilled[0] ? p.skills.find((k) => k.skill === slot.skill_id) : null, sk = SKILL[slot.skill_id];
      put(slot, p, rec ? "freshest_skill" : "free_to_help", rec ? { skill_short: sk ? sk.short_name || sk.name : slot.skill_id, day_label: "on " + rec.last_practiced } : {});
    });
    return slots.map((s) => out.find((r) => r.role_id === s.role_id)).filter(Boolean);
  }
  /* supplies packed here and their cover: uses left at the highest per-use rate any plan step declares (computed, never typed) */
  function supplies(envId) {
    if (!KM || !KM.supplies) return [];
    const st = Object.fromEntries(((KM.stock || {})[envId] || []).map((s) => [s.supply_id, s]));
    const uses = OP ? OP.plans.flatMap((p) => (p.stages || []).flatMap((s) => (s.steps || []).concat(s.prepare || []))).flatMap((s) => (s.needs || {}).supplies || []) : [];
    return KM.supplies.map((u) => {
      const q = st[u.supply_id], per = uses.filter((x) => x.supply_id === u.supply_id && x.per_use != null).map((x) => x.per_use);
      const rate = per.length ? Math.max(...per) : null;
      return { id: u.supply_id, name: u.name, unit: u.unit, consumable: u.consumable, aboard: !!q, quantity: q ? q.quantity : null,
        used: uses.some((x) => x.supply_id === u.supply_id), cover: q && q.quantity != null && rate && u.consumable !== false ? Math.floor(q.quantity / rate) : null };
    });
  }
  /* trained hands: how many crew hold each skill (the medical officer role sees this; never a health record) */
  const readiness = () => (SK ? SK.skills.map((s) => ({ id: s.skill_id, short: s.short_name || s.name, n: new Set(D.SKILL_RECORDS.filter((r) => r.skill === s.skill_id).map((r) => r.person)).size })) : []);
  /* "[P] of N have a plan aboard": rows whose plan has a step any crew member can run with this kit */
  function plansAboard(envId) {
    if (!KM || !OP) return null;
    const n = KM.conditions.filter((c) => { const v = plan(c.condition_id, envId, null, D.ME.id); return v && v.plan_aboard; }).length;
    return { aboard: n, total: KM.conditions.length };
  }
  /* a teachable skill as the screens draw it: steps, landmark, the camera check and its phrases */
  function skill(id, envId) {
    const s = SKILL[id]; if (!s) return null;
    /* a step may reuse a responder flow's step word for word (from_flow_step) */
    const flowText = (f) => (f && FLOW[f.flow_id] ? FLOW[f.flow_id][f.list][f.index] : "");
    return Object.assign({}, s, {
      steps: (s.teach_steps || []).filter((t) => !envId || !t.environments || t.environments.includes(envId)).sort((a, b) => a.step_no - b.step_no)
        .map((t) => Object.assign({}, t, { text: clean(t.text || flowText(t.from_flow_step)), landmark: LANDMARK[t.landmark_id] || null })),
      check: (s.checks || [])[0] || null,
    });
  }

  return {
    LEVEL, BY, ME: D.ME, CREW: D.CREW, PERSON: D.PERSON, ENVS, HELP, KIT,
    plan, plansAboard, skill, crewNow, person, supplies, readiness, proposeRoles, ROLE_RULES: RR,
    HELP_PRESETS: D.HELP_PRESETS, REDFLAG_FLOW: D.REDFLAG_FLOW, DEMO: D.DEMO,
    REQUEST: D.REQUEST, RECORDS: D.RECORDS, PROVENANCE: D.PROVENANCE, RESULTS: D.RESULTS,
    ROLES: D.ROLES, SKILL_RECORDS: D.SKILL_RECORDS, LINK_DEMO: D.LINK_DEMO, EPISODE: D.EPISODE,
    COPY: C["ui-copy.json"] ? C["ui-copy.json"].document : {},
    NOTICES: Object.fromEntries(Object.entries(C).map(([k, v]) => [k, (v.document && v.document.notice) || ""])),
    task, eventItems, cites, versions: Object.fromEntries(Object.entries(C).map(([k, v]) => [k, v.version.slice(0, 12)])),
  };
})();
