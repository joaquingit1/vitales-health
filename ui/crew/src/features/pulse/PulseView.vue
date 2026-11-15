<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watchEffect } from "vue";
import { useRoute } from "vue-router";
import { useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { useStage } from "../../shell/stage";
import { copy } from "../../design/copy";
import SourceWord from "../../design/SourceWord.vue";
import GapCard from "./GapCard.vue";
import {
  FINISHED,
  command,
  deviceWords,
  refusal,
  sleep,
  type Capability,
  type MeasurementRequest,
  type Observation,
} from "./hub";
import { WORDS } from "./words";

type Phase = "idle" | "starting" | "reading" | "failed" | "done" | "refused";
interface Tried {
  readonly at: string;
  readonly outcome: "success" | "failed" | "rejected";
  readonly reason: string | null;
  readonly observation: Observation | null;
}

const api = useCrewApi();
const session = useSession();
const route = useRoute();
const episodeId = computed(() => (typeof route.query["episode"] === "string" ? route.query["episode"] : null));

const capability = ref<Capability | null>(null);
const phase = ref<Phase>("idle");
const request = ref<MeasurementRequest | null>(null);
const tries = ref<Tried[]>([]);
const problem = ref("");
let alive = true;

const last = computed(() => tries.value[tries.value.length - 1] ?? null);
const reading = computed(() => (phase.value === "done" ? (last.value?.observation ?? null) : null));
const cameras = computed(() => {
  const ids = capability.value?.device_ids ?? [];
  const seen = reading.value?.device_id;
  if (ids.length > 0) return ids;
  return seen ? [seen] : [];
});
const hm = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
const lede = computed(() =>
  phase.value === "reading" || phase.value === "starting"
    ? WORDS.ledeReading
    : phase.value === "failed"
      ? WORDS.ledeFailed
      : request.value
        ? WORDS.ledeRequested
        : WORDS.ledeIdle,
);

onMounted(async () => {
  try {
    const caps = await api.get<{ items: Capability[] }>("/v1/capabilities");
    capability.value = caps.items.find((c) => c.code === "pulse") ?? null;
  } catch (failure) {
    problem.value = refusal(failure);
  }
});
/** The stage: the face lights while the cameras read; the measured dot lands on the region, beside the test not aboard. */
const stage = useStage();
watchEffect(() => {
  const region = capability.value?.body_region ?? null;
  const reading = phase.value === "reading" || phase.value === "starting" || phase.value === "failed";
  stage.headline = reading ? deviceWords(capability.value?.instrument_ids[0]) : WORDS.pulseTitle;
  stage.focus = reading ? "head" : region;
  stage.marks =
    phase.value === "done" && region
      ? [{ region, kind: "dot" }, { region, kind: "open" }]
      : !reading && region
        ? [{ region, kind: "hollow" }]
        : [];
});
onBeforeUnmount(() => {
  alive = false;
  stage.marks = [];
  stage.focus = null;
});

/** E05: propose the request, then walk its own allowed actions (submit, accept, start) with If-Match. */
async function startRequest(): Promise<MeasurementRequest> {
  let current = await api.post<MeasurementRequest>("/v1/measurement-requests", {
    capability_code: "pulse",
    reason: WORDS.pulseReason,
    decision_affected: WORDS.pulseDecision,
    body_region: capability.value?.body_region ?? "heart",
    ...(episodeId.value ? { episode_id: episodeId.value } : {}),
  });
  request.value = current;
  for (let step = 0; step < 3 && current.state !== "in_progress"; step += 1) {
    const action = ["submit", "accept", "start"].find((a) => current.allowed_actions.includes(a as never));
    if (!action) break;
    current = await command<MeasurementRequest>(
      session,
      `/v1/measurement-requests/${current.request_id}/actions`,
      { action },
      current.revision,
    );
    request.value = current;
  }
  return current;
}

/** E06: the camera worker records the attempt; the screen waits for it on the request, bounded. */
async function awaitAttempt(id: string): Promise<MeasurementRequest> {
  for (let poll = 0; poll < 120 && alive; poll += 1) {
    const current = await api.get<MeasurementRequest>(`/v1/measurement-requests/${id}`);
    request.value = current;
    if (current.attempts.length > 0 || FINISHED.has(current.state)) return current;
    await sleep(1000);
  }
  throw new Error("no attempt within the wait");
}

async function measure(): Promise<void> {
  problem.value = "";
  phase.value = "starting";
  try {
    const started = await startRequest();
    phase.value = "reading";
    const done = await awaitAttempt(started.request_id);
    const attempt = done.attempts[done.attempts.length - 1];
    if (!attempt) {
      tries.value.push({ at: new Date().toISOString(), outcome: "failed", reason: done.state, observation: null });
      phase.value = "failed";
      return;
    }
    const obsId = attempt.outcome === "success" ? attempt.observation_ids[0] : undefined;
    const observation = obsId ? await api.get<Observation>(`/v1/observations/${obsId}`) : null;
    tries.value.push({
      at: attempt.ended_at ?? attempt.started_at,
      outcome: attempt.outcome,
      reason: attempt.failure_reason,
      observation,
    });
    phase.value = observation ? "done" : "failed";
  } catch (failure) {
    problem.value = refusal(failure);
    phase.value = "refused";
  }
}

async function cancel(): Promise<void> {
  const r = request.value;
  if (!r || !r.allowed_actions.includes("cancel" as never)) return;
  try {
    request.value = await command<MeasurementRequest>(
      session,
      `/v1/measurement-requests/${r.request_id}/actions`,
      { action: "cancel" },
      r.revision,
    );
  } catch (failure) {
    problem.value = refusal(failure);
  }
}

const bars = computed(() => {
  const values = tries.value.map((t) => (t.observation ? Number(t.observation.value) : 0));
  const top = Math.max(120, ...values);
  return tries.value.map((t) => ({
    at: hm(t.at),
    kind: t.observation ? (t.observation.origin_mode === "LIVE" ? "live" : "sim") : "none",
    value: t.observation ? Number(t.observation.value) : null,
    unit: t.observation?.unit ?? "",
    height: t.observation ? `${String((Number(t.observation.value) / top) * 78)}%` : "6px",
  }));
});
const unitWords = (unit: string | undefined): string => (unit === "/min" ? "bpm" : (unit ?? ""));
defineExpose({ phase });
</script>

<template>
  <section
    class="cards flow f-measure"
    aria-label="Measurement"
  >
    <article
      class="card paper m-main"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ WORDS.pulseTitle }}
        </h2>
        <span
          v-if="capability?.expected_duration_seconds"
          class="tag"
        >{{ copy("guided_check_words.seconds_left", { n: capability.expected_duration_seconds }) }}</span>
      </div>
      <p
        class="sub"
        data-test="lede"
      >
        {{ lede }}
      </p>
      <div class="cams">
        <div
          v-for="cam in cameras.length ? cameras : ['']"
          :key="cam"
          class="cam"
          :class="{
            scan: phase === 'reading' || phase === 'starting',
            ok: phase === 'done' && reading?.device_id === cam,
            off: phase === 'failed' || (phase === 'done' && reading?.device_id !== cam),
          }"
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
          <span>{{ cam ? deviceWords(cam) : (capability?.instrument_ids[0] ? deviceWords(capability.instrument_ids[0]) : "") }}</span>
        </div>
      </div>

      <div
        v-if="phase === 'failed' && last"
        data-test="no-reading"
      >
        <div class="result">
          <span class="r-num word">{{ copy("designed_states.no_reading") }}</span>
          <span class="tag watch">{{ WORDS.keptFailed }}</span>
        </div>
        <div class="rows-l">
          <span><i>{{ WORDS.why }}</i>{{ last.reason }}</span>
          <span><i>{{ WORDS.taken }}</i>{{ hm(last.at) }}</span>
        </div>
      </div>
      <div
        v-else-if="phase === 'done' && reading"
        data-test="reading"
      >
        <div class="result">
          <div class="big">
            {{ reading.value }}<small>{{ unitWords(reading.unit) }}</small>
          </div>
          <span class="tag"><SourceWord :origin="reading.origin_mode" />, {{ deviceWords(reading.device_id) }}</span>
        </div>
        <div class="rows-l">
          <span><i>{{ WORDS.taken }}</i>{{ hm(reading.observed_at) }}</span>
          <span><i>{{ WORDS.quality }}</i>{{ reading.quality_status }}</span>
        </div>
      </div>
      <p
        v-else-if="phase === 'refused'"
        class="note"
        data-test="refused"
      >
        {{ problem }}
      </p>

      <div class="acts">
        <button
          v-if="phase === 'idle' || phase === 'refused'"
          class="pill do"
          type="button"
          :disabled="!capability || capability.status !== 'available'"
          @click="measure"
        >
          {{ WORDS.start }}
        </button>
        <button
          v-if="phase === 'failed'"
          class="pill do"
          type="button"
          @click="measure"
        >
          {{ WORDS.tryAgain }}
        </button>
        <button
          v-if="phase === 'reading'"
          class="pill"
          type="button"
          @click="cancel"
        >
          {{ WORDS.cancel }}
        </button>
      </div>
      <p
        v-if="problem && phase !== 'refused'"
        class="fine"
      >
        {{ problem }}
      </p>
    </article>

    <article
      class="card paper m-today"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ WORDS.todayTitle }}
        </h2>
      </div>
      <div
        class="bars"
        :style="{ gridTemplateColumns: `repeat(${String(Math.max(3, bars.length))}, 1fr)` }"
      >
        <div
          v-for="(b, i) in bars"
          :key="i"
          class="bar"
          :class="b.kind"
        >
          <em
            v-if="b.value === null"
            style="bottom: 34px"
          >{{ copy("designed_states.no_reading") }}</em>
          <em
            v-else
            :style="{ bottom: `calc(${b.height} + 22px)` }"
          >{{ b.value }} {{ unitWords(b.unit) }}</em>
          <i :style="{ height: b.height }" />
          <span>{{ b.at }}</span>
        </div>
      </div>
      <div class="swatches">
        <span class="s-sim"><i />{{ copy("origin_words.SIM") }}</span>
        <span class="s-live"><i />{{ copy("origin_words.LIVE") }}</span>
        <span><i style="background: var(--tile)" />{{ WORDS.noReadingKept }}</span>
      </div>
    </article>

    <GapCard
      v-if="capability"
      class="m-about"
      :region="capability.body_region"
      :episode-id="episodeId"
    />
  </section>
</template>
