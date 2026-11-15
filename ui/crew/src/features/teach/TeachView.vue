<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watchEffect } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { useStage } from "../../shell/stage";
import { copy } from "../../design/copy";
import SourceWord from "../../design/SourceWord.vue";
import {
  command,
  firstName,
  refusal,
  type GuidedCheck,
  type PlanRun,
  type RoleAssignment,
  type TeachSteps,
} from "../pulse/hub";
import { WORDS, fill } from "../pulse/words";
import { HANDOFF_KEY } from "./handoff";

const api = useCrewApi();
const session = useSession();
const route = useRoute();
const router = useRouter();
const runId = computed(() => String(route.params["id"] ?? ""));

const run = ref<PlanRun | null>(null);
const helper = ref<RoleAssignment | null>(null);
/* the plan's stage label ("Check it another way"): the card's title until a helper or the check names it */
const anotherLabel = ref("");
const check = ref<GuidedCheck | null>(null);
const steps = ref<TeachSteps | null>(null);
const problem = ref("");
const busy = ref(false);
const now = ref(0);
let countStart = 0;
let ticker = 0;
let tapped = 0;
const ROLE_POLLS = 8;
const ROLE_POLL_MS = 750;

const doneNos = computed(() => new Set((check.value?.steps_done ?? []).map((s) => s.step_no)));
const counted = computed(() => (check.value?.beat_count ?? 0) > 0 || check.value?.state === "counting");
/** The pad's window ends no later than the hub's: it opens when start_count is sent, before the hub opens its own. */
function ended(at: number): boolean {
  const c = check.value;
  return !!c && c.state === "counting" && !!c.count_seconds && at - countStart >= c.count_seconds * 1000;
}
const windowOver = computed(() => ended(now.value));
/** The step on screen: the first not done; the count stays until its window ends; then the question. */
const current = computed(() => {
  const all = steps.value?.teach_steps ?? [];
  const c = check.value;
  if (!c) return null;
  if (c.state === "counting") {
    return windowOver.value ? (all.find((s) => s.kind === "question") ?? null) : (all.find((s) => s.kind === "count") ?? null);
  }
  if (c.state === "checked" || c.state === "retry") return null;
  return all.find((s) => !doneNos.value.has(s.step_no) && !(s.kind === "count" && counted.value)) ?? null;
});
const shown = computed(() => {
  const all = steps.value?.teach_steps ?? [];
  const at = current.value ? all.indexOf(current.value) : all.length - 1;
  return all.slice(0, at + 1);
});
const left = computed(() => {
  const c = check.value;
  if (!c?.count_seconds) return 0;
  if (c.state !== "counting") return c.count_seconds;
  return Math.max(0, Math.ceil(c.count_seconds - (now.value - countStart) / 1000));
});
const ticks = computed(() => {
  const c = check.value;
  const seconds = c?.count_seconds ?? 0;
  if (!c?.count_started_at || seconds <= 0) return [];
  const t0 = Date.parse(c.count_started_at);
  return c.beats.map((b) => ((Date.parse(b) - t0) / 1000 / seconds) * 600);
});
const officer = computed(() => helper.value?.context_reasons.find((r) => r.reason_code === "officer_on_eva") ?? null);
const landmark = computed(() => current.value?.landmark_label ?? shown.value.map((s) => s.landmark_label).find(Boolean) ?? null);
const region = computed(() => current.value?.body_region ?? null);
const ref0 = computed(() => check.value?.camera_reference ?? null);
/** A question step shows the helper's answer after it, as one line. */
function stepLine(s: TeachSteps["teach_steps"][number]): string {
  const said = s.kind === "question" ? s.choices.find((x) => x.answer === check.value?.helper_answer)?.label : undefined;
  return said ? `${s.text} ${said}` : s.text;
}
const anotherWay = ref<string | null>(null);
/* The plan view's own step availability (getPlan): when no available crewmate holds the step's skill, the hub
   renders why from ui-copy plan_lines.needs_skill_holder ("Needs a crewmate with Pulse check practice"). */
type AnotherWayStep = { cannot_replace_text: string | null; availability?: string; availability_text?: string | null };
const noHelperReason = ref<string | null>(null);

/**
 * `id` is a plan run (prun_: the subject's view, D9: the helper row and Teach) or a guided check (gck_: the
 * check itself, which only its helper may act on, E30 section 10; on one laptop the helper's own session).
 */
async function load(): Promise<void> {
  if (runId.value.startsWith("gck")) {
    await open(runId.value);
    return;
  }
  try {
    run.value = await api.get<PlanRun>(`/v1/plan-runs/${runId.value}`);
  } catch (failure) {
    /* the run is its subject's to read (E28); its helper reaches the check the subject started on this laptop */
    const handed = readHandoff();
    if (!handed) throw failure;
    const handedCheck = await api.get<GuidedCheck>(`/v1/guided-checks/${handed}`);
    if (handedCheck.plan_run_id !== runId.value) throw failure;
    await open(handed);
    return;
  }
  const roles = await proposedRoles(runId.value);
  helper.value = roles.items.find((r) => r.skill_id !== null && r.state !== "changed") ?? roles.items[0] ?? null;
  const plan = await api.get<{ stages: { stage_id: string; label?: string; steps: AnotherWayStep[] }[] }>(
    `/v1/plans/${run.value.plan_id}`,
  );
  const another = plan.stages.find((s) => s.stage_id === "check_another_way");
  const first = another?.steps[0];
  anotherWay.value = first?.cannot_replace_text ?? null;
  noHelperReason.value = first?.availability === "needs_skill_holder" ? (first.availability_text ?? null) : null;
  anotherLabel.value = another?.label ?? "";
  const existing = run.value.steps.find((s) => s.stage_id === "check_another_way" && s.guided_check_id)?.guided_check_id;
  if (existing) await open(existing);
}

/** The steps are the helper's to read (E30 section 10); the subject sees the check and who takes the steps. */
/** E31 proposes the run's roles from the run's start event, a moment after the run exists: a bounded wait for them. */
async function proposedRoles(id: string): Promise<{ items: RoleAssignment[] }> {
  let roles = await api.get<{ items: RoleAssignment[] }>(`/v1/plan-runs/${id}/roles`);
  for (let i = 0; i < ROLE_POLLS && roles.items.length === 0; i += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, ROLE_POLL_MS));
    roles = await api.get<{ items: RoleAssignment[] }>(`/v1/plan-runs/${id}/roles`);
  }
  return roles;
}

async function open(id: string): Promise<void> {
  const found = await api.get<GuidedCheck>(`/v1/guided-checks/${id}`);
  check.value = found;
  steps.value = session.current?.person?.person_id === found.helper.person_id
    ? await api.get<TeachSteps>(`/v1/guided-checks/${id}/steps`)
    : null;
}

async function guard(work: () => Promise<void>): Promise<void> {
  busy.value = true;
  problem.value = "";
  try {
    await work();
  } catch (failure) {
    problem.value = refusal(failure);
  } finally {
    busy.value = false;
  }
}

/** E31 then E30: the helper accepts the proposed role in their own session, then the check starts on the run. */
const teach = (): Promise<void> =>
  guard(async () => {
    const h = helper.value;
    const r = run.value;
    if (!h || !r) return;
    let assignment = h;
    if (h.state === "proposed" && h.allowed_actions.includes("accept" as never) && session.current?.person?.person_id === h.person.person_id) {
      assignment = await command<RoleAssignment>(
        session,
        `/v1/plan-runs/${r.plan_run_id}/roles`,
        { action: "accept", role_assignment_id: h.role_assignment_id, client_time: new Date().toISOString() },
        h.revision,
      );
      helper.value = assignment;
    }
    const started = await api.post<GuidedCheck>("/v1/guided-checks", {
      skill_id: assignment.skill_id,
      helper_person_id: assignment.person.person_id,
      subject_person_id: r.subject.person_id,
      plan_run_id: r.plan_run_id,
      ...(assignment.state === "accepted" ? { role_assignment_id: assignment.role_assignment_id } : {}),
    });
    rememberHandoff(started.guided_check_id);
    await open(started.guided_check_id);
    await router.replace({ name: "teach", params: { id: started.guided_check_id } });
  });

/** Only the helper acts on a check (E30 section 10); the id waits here for the helper's own session on this laptop. */
function rememberHandoff(id: string): void {
  try {
    window.sessionStorage.setItem(HANDOFF_KEY, id);
  } catch {
    /* without storage the helper opens the check from the subject's link */
  }
}
function readHandoff(): string | null {
  try {
    return window.sessionStorage.getItem(HANDOFF_KEY);
  } catch {
    return null;
  }
}
const isHelper = computed(() => !!check.value && session.current?.person?.person_id === check.value.helper.person_id);

async function act(body: Record<string, unknown>): Promise<GuidedCheck> {
  const c = check.value;
  if (!c) throw new Error("no check is open");
  const next = await command<GuidedCheck>(
    session,
    `/v1/guided-checks/${c.guided_check_id}/actions`,
    { ...body, client_time: new Date().toISOString() },
    c.revision,
  );
  check.value = next;
  return next;
}

const nextStep = (): Promise<void> =>
  guard(async () => {
    if (current.value) await act({ action: "step_done", step_no: current.value.step_no });
  });

/** One tap is one Beat event; the hub stamps its own receive time and the tick is drawn from it. */
async function beat(): Promise<void> {
  const c = check.value;
  if (!c || ended(performance.now())) return;
  try {
    if (c.state === "teaching") {
      const sent = performance.now();
      await act({ action: "start_count" });
      countStart = sent;
      tapped = 0;
    }
    tapped += 1;
    const id = check.value?.guided_check_id ?? c.guided_check_id;
    check.value = await command<GuidedCheck>(session, `/v1/guided-checks/${id}/beats`, {
      sequence: tapped,
      client_time: new Date().toISOString(),
    });
  } catch (failure) {
    /* a tap still in flight when the count ends is late, not a problem: the hub keeps the count it has */
    if (!ended(performance.now())) problem.value = refusal(failure);
  }
}

const answer = (value: string): Promise<void> =>
  guard(async () => {
    await act({ action: "answer", answer: value });
    await act({ action: "finish" });
  });

const again = (): Promise<void> =>
  guard(async () => {
    await act({ action: "retry" });
    tapped = 0;
  });

onMounted(() => {
  ticker = window.setInterval(() => {
    now.value = performance.now();
  }, 250);
  void guard(load);
});
/** The stage: the step's region in focus and its landmark lit. */
const stage = useStage();
watchEffect(() => {
  const at = region.value ?? shown.value.map((s) => s.body_region).find(Boolean) ?? null;
  stage.headline = landmark.value ?? steps.value?.card_title_text ?? helper.value?.task_text ?? anotherLabel.value;
  stage.focus = at;
  stage.marks = at ? [{ region: at, kind: "dot" }] : [];
});
onBeforeUnmount(() => {
  window.clearInterval(ticker);
  stage.marks = [];
  stage.focus = null;
});
</script>

<template>
  <section
    class="cards flow f-teach"
    aria-label="A crewmate checks"
  >
    <article
      class="card paper t-main"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ steps?.card_title_text ?? helper?.task_text ?? anotherLabel }}
        </h2>
        <span
          v-if="steps"
          class="tag"
        >{{ steps.notice }}</span>
      </div>
      <div
        v-if="problem"
        class="result"
      >
        <span
          class="r-num word"
          data-test="problem"
        >{{ problem }}</span>
      </div>

      <div
        v-if="!problem && !check && run && !helper"
        class="result"
        data-test="no-helper"
      >
        <span class="r-num word">{{ noHelperReason ?? copy("designed_states.not_available_here") }}</span>
      </div>

      <template v-if="!check">
        <div
          v-if="helper"
          class="list"
          data-test="helper-row"
        >
          <div class="li">
            <span class="what">{{ helper.person.display_name }}<small>{{ helper.reason_text }}</small></span>
          </div>
          <div
            v-if="officer"
            class="li"
          >
            <span
              class="what"
              data-test="officer"
            >{{ officer.text }}.</span>
          </div>
        </div>
        <div class="acts">
          <button
            v-if="helper"
            class="pill do"
            type="button"
            :disabled="busy"
            data-test="teach"
            @click="teach"
          >
            {{ WORDS.teach }} {{ firstName(helper.person.display_name) }}
          </button>
        </div>
      </template>

      <template v-else>
        <p
          v-if="!isHelper && !check.result"
          class="sub"
          data-test="handoff"
        >
          {{ fill(WORDS.handoff, { helper: firstName(check.helper.display_name) }) }}
        </p>
        <p
          v-if="steps && current"
          class="sub"
        >
          {{ fill(WORDS.stepOf, { i: current.step_no, n: steps.teach_steps.length }) }}
        </p>
        <div class="nsteps t-steps">
          <div
            v-for="s in shown"
            :key="s.step_no"
            class="li"
            :class="s === current ? 'now' : 'done'"
            data-test="step"
          >
            <span class="what">{{ stepLine(s) }}</span>
          </div>
        </div>

        <div
          v-if="current?.kind === 'instruction'"
          class="acts"
        >
          <button
            class="pill do"
            type="button"
            :disabled="busy"
            data-test="next"
            @click="nextStep"
          >
            {{ WORDS.nextStep }}
          </button>
        </div>
        <div v-else-if="current?.kind === 'count'">
          <button
            class="pill do beat"
            type="button"
            :disabled="windowOver || left === 0"
            data-test="beat"
            @click="beat"
          >
            {{ copy("guided_check_words.beat_button") }}
          </button>
          <div class="rows-l">
            <span><i>{{ WORDS.tapsLabel }}</i><b data-test="taps">{{ check.beat_count }}</b></span>
            <span><i>{{ WORDS.leftLabel }}</i><b class="secs">{{ copy("guided_check_words.seconds_left", { n: left }) }}</b></span>
          </div>
        </div>
        <div
          v-else-if="current?.kind === 'question'"
          class="pills"
        >
          <button
            v-for="ch in current.choices"
            :key="ch.answer"
            class="pill"
            type="button"
            :disabled="busy"
            :data-test="`answer-${ch.answer}`"
            @click="answer(ch.answer)"
          >
            {{ ch.label }}
          </button>
        </div>

        <div
          v-if="check.result"
          data-test="verdict"
        >
          <p
            class="note"
            :style="{ color: check.result === 'agree' ? 'var(--green-2)' : 'var(--ink-2)' }"
          >
            {{ check.result === "disagree" ? copy("guided_check_words.result_disagree") + " " : "" }}{{ check.result_text }}
          </p>
          <p
            v-if="check.count_per_minute !== null"
            class="note"
            data-test="count-line"
          >
            {{ copy("guided_check_words.your_count", { n: check.count_per_minute }) }}
            <SourceWord :origin="check.origin_mode" />
          </p>
          <p
            class="note"
            data-test="ring-line"
          >
            {{ check.what_it_shows?.does_not ?? "" }} {{ anotherWay ?? "" }}
          </p>
          <div class="acts">
            <button
              v-if="check.allowed_actions.includes('retry' as never)"
              class="pill"
              type="button"
              :disabled="busy"
              @click="again"
            >
              {{ WORDS.countAgain }}
            </button>
          </div>
        </div>
      </template>
    </article>

    <article
      class="card paper t-taps"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ WORDS.taps }}
        </h2>
        <span class="tag">{{ copy("guided_check_words.seconds_left", { n: check?.count_seconds ?? 0 }) }}</span>
      </div>
      <div
        class="taps"
        role="img"
        :aria-label="`${String(check?.beat_count ?? 0)} taps`"
      >
        <svg
          viewBox="0 0 600 60"
          preserveAspectRatio="none"
        >
          <line
            x1="0"
            y1="44"
            x2="600"
            y2="44"
            stroke="var(--line)"
            stroke-width="1"
            vector-effect="non-scaling-stroke"
          />
          <line
            v-for="(x, i) in ticks"
            :key="i"
            :x1="x"
            y1="16"
            :x2="x"
            y2="44"
            stroke="var(--ink)"
            stroke-width="1.4"
            vector-effect="non-scaling-stroke"
            data-test="tick"
          />
        </svg>
      </div>
      <p class="fine">
        {{ WORDS.tapsNote }}
      </p>
    </article>

    <article
      class="card paper t-about"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ WORDS.aboutCheck }}
        </h2>
        <span
          v-if="check?.experimental"
          class="tag"
        >{{ copy("guided_check_words.experimental") }}</span>
      </div>
      <div class="about-cam">
        <div class="cams">
          <div
            class="cam"
            :class="{ scan: check?.state === 'counting', ok: !!ref0 && ref0.value !== null, off: !!ref0 && ref0.value === null }"
          >
            <svg
              class="icon"
              viewBox="0 0 24 24"
            ><ellipse
              cx="12"
              cy="11"
              rx="6"
              ry="7.5"
            /><path d="M9.5 10h.01M14.5 10h.01M10 14.5c1.2.8 2.8.8 4 0" /></svg>
          </div>
        </div>
        <div
          v-if="ref0"
          class="result"
          data-test="camera-reference"
        >
          <template v-if="ref0.value !== null">
            <div class="big">
              {{ ref0.value }}<small>{{ ref0.unit === "/min" ? "bpm" : ref0.unit }}</small>
            </div>
            <SourceWord :origin="ref0.origin_mode" />
          </template>
          <template v-else>
            <span class="r-num word">{{ copy("designed_states.no_reading") }}</span>
            <span class="tag watch">{{ ref0.no_reading_reason }}</span>
          </template>
        </div>
        <p
          v-else
          class="sub"
        >
          {{ WORDS.compared }}
        </p>
      </div>
      <template v-if="check?.what_it_shows">
        <p class="label-k">
          {{ WORDS.doesAndNot }}
        </p>
        <div class="list">
          <div class="li">
            <span class="what">{{ check.what_it_shows.does }}</span>
          </div>
          <div class="li">
            <span class="what">{{ check.what_it_shows.does_not }}</span>
          </div>
        </div>
      </template>
      <p
        v-if="check?.tolerance"
        class="fine"
        style="margin-top: 14px"
      >
        {{ WORDS.agreement }}: {{ check.tolerance.value }} {{ check.tolerance.unit }}, {{ check.tolerance.basis }}, {{ check.tolerance.to_be_set_by }}.
      </p>
    </article>
  </section>
</template>
