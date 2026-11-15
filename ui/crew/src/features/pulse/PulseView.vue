<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watchEffect } from "vue";
import { RouterLink, useRoute } from "vue-router";
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
import { pulseReadings, rememberPulseRequest } from "../today/cards/pulseReadings";

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
/* a finished reading closes the request: the line says when it was done (ui-copy occurrence_end_text.done_today) */
const doneLine = (iso: string): string => {
  const text = copy("occurrence_end_text.done_today", { time: hm(iso) });
  return (text[0] ?? "").toUpperCase() + text.slice(1);
};
const lede = computed(() =>
  phase.value === "reading" || phase.value === "starting"
    ? WORDS.ledeReading
    : phase.value === "failed"
      ? WORDS.ledeFailed
      : phase.value === "done" && reading.value
        ? doneLine(reading.value.observed_at)
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
  /* Pulse today starts with the readings this session already recorded (the same reads as the Today card) */
  const earlier = await pulseReadings(api).catch(() => []);
  if (alive && tries.value.length === 0) {
    tries.value = earlier.map((observation) => ({
      at: observation.observed_at,
      outcome: "success" as const,
      reason: null,
      observation,
    }));
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
  rememberPulseRequest(current.request_id);
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
    if (observation) {
      stage.recorded += 1;
    }
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
        <RouterLink
          class="go"
          :to="{ name: 'today' }"
          :aria-label="copy('display_group_labels.today')"
        >
          <svg
            class="icon"
            viewBox="0 0 24 24"
          ><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>
        </RouterLink>
      </div>
      <div class="m-body">
        <div class="cams m-cams">
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
        <div class="m-side">
          <p
            class="sub"
            data-test="lede"
          >
            {{ lede }}
          </p>
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
          <div
            v-else-if="phase === 'refused'"
            class="result"
          >
            <span
              class="r-num word"
              data-test="refused"
            >{{ problem }}</span>
          </div>

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
        </div>
      </div>
    </article>

    <article
      class="card paper m-today"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ WORDS.todayTitle }}
        </h2>
      </div>
      <p
        v-if="bars.length === 0"
        class="sub"
        data-test="today-empty"
      >
        {{ copy("designed_states.no_reading") }}
      </p>
      <div
        v-else
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

<style scoped>
.go { color: inherit; }
/* Content flows top down and each card sizes to it: no row stretches a card into a blank half. */
.cards.f-measure { grid-template-rows: auto auto; align-content: start; }
.f-measure > .card { align-self: start; }
/* the camera tile is the card's hero; the instruction, Start and the result sit beside it */
.m-body { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 20px; align-items: start; margin-top: 14px; }
.m-cams { grid-template-columns: minmax(0, 1fr); margin-top: 0; }
.m-cams .cam { aspect-ratio: 16 / 9; }
.m-cams .cam .icon { width: 18%; }
.m-side { display: flex; flex-direction: column; min-width: 0; }
.m-side .acts { margin-top: 0; }
.m-today .bars { flex: none; height: 150px; margin-top: 30px; }
.m-today .sub { margin-top: 6px; }
@media (max-width: 720px) {
  .m-body { grid-template-columns: minmax(0, 1fr); }
  .f-measure .m-today { height: auto; }
}
</style>
