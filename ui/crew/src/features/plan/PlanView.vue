<script setup lang="ts">
import { computed, onMounted, ref, watch, watchEffect } from "vue";
import { useRoute } from "vue-router";
import { ApiProblem, useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { useStage } from "../../shell/stage";
import { PLAN_CARD_TITLE, START_PLAN } from "../help/labels";
import { groundLine, planCountLine, shownStages, startedAt, type KitMapView, type PlanRun, type PlanView } from "./planModel";
import { copy } from "../../design/copy";

const api = useCrewApi();
const session = useSession();
const route = useRoute();
const stage = useStage();

const plan = ref<PlanView | null>(null);
const planError = ref<string | null>(null);
const kitMap = ref<KitMapView | null>(null);
const kitMapError = ref<string | null>(null);
const run = ref<PlanRun | null>(null);
const starting = ref(false);
const startError = ref<string | null>(null);

const id = computed(() => String(route.params["id"] ?? ""));
const episodeId = computed(() => {
  const value = route.query["episode"];
  return typeof value === "string" && value !== "" ? value : null;
});

function refused(failure: unknown): string {
  if (failure instanceof ApiProblem) {
    const code = failure.code ?? "INTERNAL_ERROR";
    const message = copy(`error_messages.${code}`);
    return message.startsWith("error_messages.") ? code : message;
  }
  return copy("error_messages.INTERNAL_ERROR");
}

async function loadPlan(): Promise<void> {
  plan.value = null;
  planError.value = null;
  run.value = null;
  const target = id.value;
  try {
    if (target.startsWith("plan_")) {
      plan.value = await api.get<PlanView>(`/v1/plans/${encodeURIComponent(target)}`);
    } else if (/^ICL\d+$/.test(target)) {
      plan.value = await api.get<PlanView>(`/v1/conditions/${encodeURIComponent(target)}/plan`);
    } else {
      const opened = await api.get<PlanRun>(`/v1/plan-runs/${encodeURIComponent(target)}`);
      run.value = opened;
      plan.value = await api.get<PlanView>(`/v1/plans/${encodeURIComponent(opened.plan_id)}`);
    }
  } catch (failure) {
    planError.value = refused(failure);
    return;
  }
  if (run.value === null) {
    try {
      const runs = await api.get<{ items: PlanRun[] }>("/v1/plan-runs");
      run.value = runs.items.find((x) => x.plan_id === plan.value?.plan_id && x.state !== "closed") ?? null;
    } catch {
      run.value = null;
    }
  }
}

async function loadKitMap(): Promise<void> {
  try {
    kitMap.value = await api.get<KitMapView>("/v1/kit-map");
  } catch (failure) {
    kitMap.value = null;
    kitMapError.value = refused(failure);
  }
}

async function start(): Promise<void> {
  const shown = plan.value;
  const subject = session.current?.person?.person_id;
  if (shown === null || subject === undefined || episodeId.value === null) {
    return;
  }
  starting.value = true;
  startError.value = null;
  try {
    run.value = await api.post<PlanRun>("/v1/plan-runs", {
      plan_id: shown.plan_id,
      subject_person_id: subject,
      episode_id: episodeId.value,
    });
  } catch (failure) {
    startError.value = refused(failure);
  } finally {
    starting.value = false;
  }
}

onMounted(() => {
  void loadPlan();
  void loadKitMap();
});
watch(id, () => void loadPlan());

const stages = computed(() => (plan.value === null ? [] : shownStages(plan.value)));
const ground = computed(() => (plan.value === null ? null : groundLine(plan.value)));
const count = computed(() => (kitMap.value === null ? null : planCountLine(kitMap.value)));
const who = computed(() => session.current?.person?.display_name ?? session.current?.actor.display_name ?? "");
const runLine = computed(() => {
  const current = run.value;
  if (current === null) {
    return null;
  }
  return `${copy("plan_lines.plan_tag", { plan_label: plan.value?.condition_id ?? current.plan_id })} · ${startedAt(current)} · ${current.state}`;
});
watchEffect(() => {
  stage.subline = who.value;
  stage.headline = plan.value?.title ?? "";
  stage.focus = null;
  stage.marks = [];
});
const canStart = computed(() => run.value === null && episodeId.value !== null && session.current?.person != null);
</script>

<template>
  <section
    class="cards flow f-plan"
    aria-label="The plan aboard"
  >
    <article
      class="card g g-deep plan-main"
      aria-labelledby="plan-card-title"
    >
      <div class="card-head">
        <h2
          id="plan-card-title"
          class="card-title"
        >
          {{ PLAN_CARD_TITLE }}
          <span
            v-if="plan"
            class="tag plan-mode"
            data-test="plan-mode"
          >{{ plan.mode_text }}</span>
        </h2>
        <div
          v-if="plan"
          class="pills plan-acts"
        >
          <button
            v-if="run || canStart"
            class="pill do"
            type="button"
            :disabled="run !== null || starting"
            data-test="plan-start"
            @click="start"
          >
            {{ runLine ?? START_PLAN }}
          </button>
        </div>
      </div>
      <p
        v-if="planError"
        class="say"
        data-test="plan-refused"
      >
        {{ copy("designed_states.not_available_here") }}
      </p>
      <p
        v-if="planError"
        class="fine"
      >
        {{ planError }}
      </p>
      <div
        v-else-if="plan"
        class="nsteps across plan-steps"
        data-test="plan-stages"
      >
        <div
          v-for="s in stages"
          :key="s.id"
          class="li"
          :class="{ done: run !== null && s.id === 'do_now' }"
          :data-stage="s.id"
        >
          <span class="what"><b>{{ s.word }}</b><span class="t">{{ s.text }}</span><small v-if="s.note">{{ s.note }}</small></span>
        </div>
      </div>
      <p
        v-if="startError"
        class="fine"
      >
        {{ startError }}
      </p>
      <p
        v-if="plan"
        class="fine plan-fine"
        data-test="plan-ground"
      >
        {{ plan.tag_text }}. <span data-test="plan-ground-line">{{ ground ?? "" }}</span>
      </p>
    </article>

    <article
      class="card paper plan-ground"
      aria-labelledby="plan-ground-title"
    >
      <div class="card-head">
        <h2
          id="plan-ground-title"
          class="card-title"
        >
          {{ copy("plan_stage_words.ground_log") }}
        </h2>
      </div>
      <p
        class="say"
        data-test="plan-ground-card"
      >
        {{ ground ?? "" }}
      </p>
      <p
        v-if="plan"
        class="fine"
      >
        {{ plan.notice }}
      </p>
    </article>

    <article
      class="card paper plan-count"
      aria-labelledby="plan-count-title"
    >
      <div class="card-head">
        <h2
          id="plan-count-title"
          class="card-title"
          data-test="plan-count"
        >
          {{ count ?? kitMapError ?? "" }}
        </h2>
      </div>
      <p
        v-if="kitMap && kitMap.plans_to_write.length > 0"
        class="say"
      >
        {{ kitMap.plans_to_write.join(", ") }}
      </p>
      <p
        v-if="kitMap"
        class="fine"
      >
        {{ kitMap.kit_map_label }}
      </p>
    </article>
  </section>
</template>

<style scoped>
.plan-main { grid-column: 1 / span 24; grid-row: 1 / span 2; }
.plan-ground { grid-column: 1 / span 14; grid-row: 3; }
.plan-count { grid-column: 15 / span 10; grid-row: 3; }
.plan-mode { margin-left: 10px; vertical-align: 2px; background: rgba(255, 255, 255, 0.16); color: #fff; }
.f-plan .card { cursor: default; }
.f-plan .card:hover { transform: none; }
.plan-acts { margin: 0; flex-wrap: nowrap; }
.plan-steps { flex: 1; min-height: 0; margin-top: 28px; }
.plan-steps.nsteps.across .what { font-size: 20px; line-height: 27px; font-weight: 300; }
.plan-steps.nsteps.across .what b { font-size: 15px; font-weight: 500; }
.plan-steps.nsteps.across .what .t { -webkit-line-clamp: 6; }
.plan-steps.nsteps.across .what small { -webkit-line-clamp: 4; font-size: 14px; line-height: 19px; }
.plan-steps .li.done::before { background: #fff; color: var(--green); }
.plan-fine { margin-top: 6px; color: rgba(255, 255, 255, 0.82); font-size: 15px; line-height: 22px; }
</style>
