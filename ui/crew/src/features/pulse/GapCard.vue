<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { components } from "@crew-health/contracts";
import { ApiProblem, useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { plainName, refusal, type Capability, type ConfigDocument, type KitMapInstrument, type KitMapView, type PlanRun } from "./hub";
import { WORDS } from "./words";

type PlanView = components["schemas"]["plan-view.schema"];
type Episode = components["schemas"]["episode.schema"];
type HelpSession = components["schemas"]["help-session.schema"];

const props = defineProps<{ region: string | null; episodeId: string | null }>();
const api = useCrewApi();
const session = useSession();
const route = useRoute();
const router = useRouter();

const gap = ref<Capability | null>(null);
const needs = ref<string[]>([]);
const forms = ref<string[]>([]);
const plan = ref<PlanView | null>(null);
const problem = ref("");
const busy = ref(false);

const headline = computed(() =>
  gap.value ? `${plainName(gap.value.label)}: needs ${needs.value.join(" or ")}.` : "",
);
const another = computed(() => {
  const stage = plan.value?.stages.find((s) => s.stage_id === "check_another_way");
  return stage && "steps" in stage ? stage : null;
});
const step = computed(() => another.value?.steps[0] ?? null);
/* The reference's "Check it another way" (11-assess, 12-teach): offered when the step is a guided check whose
   instruments and supplies are aboard. A missing skill holder is what the teach step answers, so it does not hide
   the action; the hub still decides when the run starts. */
const teachable = computed(() => {
  const s = step.value;
  if (s === null || another.value === null) return false;
  if (s.availability === "aboard") return true;
  return (
    s.guided_check_skill_id != null && s.missing.instruments.length === 0 && s.missing.supplies.length === 0
  );
});

/** E27: the test this kit lacks for the region, from the hub's capabilities, kit map view and kit-map document. */
async function load(): Promise<void> {
  const [caps, view, doc] = await Promise.all([
    api.get<{ items: Capability[] }>("/v1/capabilities"),
    api.get<KitMapView>("/v1/kit-map"),
    api.get<ConfigDocument<{ instruments: KitMapInstrument[] }>>("/v1/config/kit-map"),
  ]);
  const found = caps.items.find((c) => c.body_region === props.region && c.status !== "available" && c.missing_instrument_ids.length > 0);
  gap.value = found ?? null;
  if (!found) return;
  const missing = new Set(found.missing_instrument_ids);
  needs.value = doc.document.instruments.filter((i) => missing.has(i.id)).map((i) => i.needs_text ?? i.name);
  const conditions = view.conditions.filter((c) => c.missing_instruments.some((m) => missing.has(m)));
  forms.value = [...new Set(conditions.map((c) => c.cliff_url).filter((u): u is string => typeof u === "string"))];
  const planId = await planFor(found.code, conditions);
  if (planId) plan.value = await api.get<PlanView>(`/v1/plans/${planId}`);
}

/** The plan aboard for the gap: the episode's own gap first, else the condition the caller names. */
async function planFor(code: string, conditions: KitMapView["conditions"]): Promise<string | null> {
  if (props.episodeId) {
    const episode = await api.get<Episode>(`/v1/episodes/${props.episodeId}`);
    const own = episode.capability_gaps.find((g) => g.capability_code === code);
    if (own?.plan_id) return own.plan_id;
  }
  const named = typeof route.query["condition"] === "string" ? route.query["condition"] : null;
  /* no episode and no named condition: the first condition the kit map lists for this gap that has a plan aboard */
  return (
    conditions.find((c) => c.condition_id === named)?.plan_id ??
    conditions.find((c) => c.plan_id != null && c.plan_aboard)?.plan_id ??
    null
  );
}

/** startPlanRun on a help session; a second start answers 422 open_run_exists with the open run, which is reused. */
async function startRun(planId: string, me: string, helpSessionId: string): Promise<PlanRun> {
  try {
    return await api.post<PlanRun>("/v1/plan-runs", { plan_id: planId, subject_person_id: me, help_session_id: helpSessionId });
  } catch (failure) {
    const open = failure instanceof ApiProblem ? failure.currentState : null;
    if (typeof open === "object" && open !== null && "plan_run_id" in open && typeof open.plan_run_id === "string") {
      return open as PlanRun;
    }
    throw failure;
  }
}

/** Check it another way runs inside the plan aboard: the open run of this plan, or a new one for the episode. */
async function checkAnotherWay(): Promise<void> {
  const p = plan.value;
  const me = session.current?.person?.person_id;
  if (!p || !me) return;
  busy.value = true;
  problem.value = "";
  try {
    const runs = await api.get<{ items: PlanRun[] }>("/v1/plan-runs");
    let run = runs.items.find((r) => r.plan_id === p.plan_id && r.state !== "closed") ?? null;
    if (!run && props.episodeId) {
      run = await api.post<PlanRun>("/v1/plan-runs", { plan_id: p.plan_id, subject_person_id: me, episode_id: props.episodeId });
    }
    if (!run) {
      /* no episode: the run hangs on a help session (PlanRunCreate takes exactly one of episode_id or help_session_id).
         createHelpSession, then startPlanRun with it, then the teach step (getGuidedCheckSteps via the teach route). */
      const help = await api.post<HelpSession>("/v1/help-sessions", {
        subject: { kind: "self" },
        entry: { kind: "button" },
        client_opened_at: new Date().toISOString(),
      });
      run = await startRun(p.plan_id, me, help.help_session_id);
    }
    if (run) await router.push({ name: "teach", params: { id: run.plan_run_id } });
    else problem.value = WORDS.noPlanRunning;
  } catch (failure) {
    problem.value = refusal(failure);
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  load().catch((failure: unknown) => {
    problem.value = refusal(failure);
  });
});
</script>

<template>
  <article
    class="card paper a-gap"
    data-test="gap-card"
  >
    <div class="card-head">
      <h2 class="card-title">
        {{ WORDS.gapTitle }}
      </h2>
    </div>
    <div class="a-gap-top">
      <div class="gap-fig">
        <svg
          viewBox="0 0 200 140"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="An open dashed ring: a test this kit does not carry."
        >
          <circle
            cx="100"
            cy="70"
            r="52"
            fill="none"
            stroke="#1c1e1c"
            stroke-width="1.6"
            stroke-dasharray="5 6"
          />
          <path
            d="M58 72h22l7-18 10 34 8-26 5 10h32"
            fill="none"
            stroke="#6b6f69"
            stroke-width="1.4"
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-dasharray="2 4"
          />
        </svg>
      </div>
    </div>
    <template v-if="gap">
      <p
        class="ask-q"
        data-test="gap-headline"
      >
        {{ headline }}
      </p>
      <p class="sub">
        {{ WORDS.notAboard }}
        <a
          v-for="url in forms.slice(0, 1)"
          :key="url"
          class="cliff"
          :href="url"
          target="_blank"
          rel="noopener"
        >{{ WORDS.cliff }}</a>
      </p>
    </template>
    <template v-if="step && another">
      <p
        class="sub"
        style="margin-top: 14px"
      >
        <b style="font-weight: 500; color: var(--ink)">{{ another.label }}.</b> {{ step.text }}
        {{ step.cannot_replace_text ?? "" }}
      </p>
    </template>
    <p
      v-if="problem"
      class="fine"
      data-test="gap-problem"
    >
      {{ problem }}
    </p>
    <div
      v-if="teachable && another"
      class="acts"
    >
      <button
        class="pill do"
        type="button"
        :disabled="busy"
        data-test="check-another-way"
        @click="checkAnotherWay"
      >
        {{ another.label }}
      </button>
    </div>
    <p
      v-if="plan"
      class="fine"
    >
      {{ plan.tag_text.includes(plan.notice) ? plan.tag_text : `${plan.tag_text} · ${plan.notice}` }}
    </p>
  </article>
</template>
