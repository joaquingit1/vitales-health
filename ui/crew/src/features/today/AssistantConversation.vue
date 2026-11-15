<script setup lang="ts">
/**
 * DEMO-PLAN shot C on the Assistant card (UX-DESIGN section 5, "Ask about a reading"; section 6, steps 3 and 4):
 * the person writes a concern, the hub opens an episode and an assistant conversation in its context (E04, E13),
 * and the card shows the verified reply as it is stored. A measurement the assistant proposed is held by E05 as
 * proposed until the person confirms it here (submit, then accept, with If-Match). Nothing on this card is a value
 * the hub did not return: the reply, its limitations and the kit check come from the hub; the words come from
 * config/assistant-ui.json and config/ui-copy.json, plus the two prototype phrases in ./words.ts.
 */
import { computed, onBeforeUnmount, ref } from "vue";
import { RouterLink } from "vue-router";
import type { components } from "@crew-health/contracts";
import assistantUi from "../../../../../config/assistant-ui.json";
import { copy } from "../../design/copy";
import { useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { command, refusal, type MeasurementRequest } from "../pulse/hub";
import { ASK_WORDS } from "./words";

type S = components["schemas"];
type Conversation = S["conversation.schema"];
type Message = S["assistant-message.schema"];
type Episode = S["episode.schema"];

const props = defineProps<{ running: boolean; instruments: ReadonlyMap<string, string> }>();

const api = useCrewApi();
const session = useSession();
const LABELS = assistantUi.labels;
const STATES = assistantUi.states;
const POLL_MS = 1000;
/* bounded wait: the policy's own turn limit ends the turn on the hub well before this */
const MAX_POLLS = 600;

const draft = ref("");
const messages = ref<Message[]>([]);
const busy = ref(false);
const waited = ref(0);
const problem = ref("");
const request = ref<MeasurementRequest | null>(null);
const confirming = ref(false);
let alive = true;
let ticker = 0;

onBeforeUnmount(() => {
  alive = false;
  window.clearInterval(ticker);
});

const reply = computed(() => [...messages.value].reverse().find((m) => m.role !== "person") ?? null);
const action = computed(() => reply.value?.action ?? null);
const asked = ref("");

const kitLine = computed(() => {
  const check = request.value?.kit_check;
  if (!check) return "";
  const names = (ids: readonly string[]): string => ids.map((id) => props.instruments.get(id) ?? id).join(", ");
  return check.can
    ? copy("kit_check_templates.can", { instruments: names(check.instrument_ids) })
    : copy("kit_check_templates.cannot", { instruments: names(check.missing_instrument_ids) });
});

const confirmed = computed(() => {
  const state = request.value?.state;
  return state !== undefined && state !== "proposed" && state !== "awaiting_acceptance";
});

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/** E05: the request the assistant proposed is the episode's newest one for that capability. */
async function proposedRequest(episodeId: string, capability: string): Promise<MeasurementRequest | null> {
  const episode = await api.get<Episode>(`/v1/episodes/${encodeURIComponent(episodeId)}`);
  for (const id of [...episode.measurement_request_ids].reverse()) {
    const found = await api.get<MeasurementRequest>(`/v1/measurement-requests/${encodeURIComponent(id)}`);
    if (found.capability_code === capability) return found;
  }
  return null;
}

async function send(): Promise<void> {
  const text = draft.value.trim();
  if (text === "" || busy.value || !props.running) return;
  busy.value = true;
  problem.value = "";
  request.value = null;
  messages.value = [];
  asked.value = text;
  waited.value = 0;
  const started = Date.now();
  ticker = window.setInterval(() => (waited.value = Math.round((Date.now() - started) / 1000)), 1000);
  try {
    const episode = await api.post<Episode>("/v1/episodes", {
      concern_text: text,
      origin_mode: "SIM",
      onset: { at: null, precision: "unknown" },
    });
    const opened = await api.post<Conversation>("/v1/assistant/conversations", {
      context: { kind: "episode", ref_id: episode.episode_id },
    });
    const sent = await api.post<Message>(
      `/v1/assistant/conversations/${encodeURIComponent(opened.conversation_id)}/messages`,
      { text },
    );
    messages.value = [sent];
    draft.value = "";
    for (let poll = 0; poll < MAX_POLLS && alive; poll += 1) {
      const current = await api.get<Conversation>(
        `/v1/assistant/conversations/${encodeURIComponent(opened.conversation_id)}`,
      );
      messages.value = current.messages;
      if (current.active_turn_id === null && current.messages.some((m) => m.role !== "person")) break;
      await sleep(POLL_MS);
    }
    const proposed = action.value;
    if (proposed?.action === "request_measurement" && reply.value?.status === "complete") {
      request.value = await proposedRequest(episode.episode_id, proposed.capability_code);
    }
  } catch (failure) {
    problem.value = refusal(failure);
  } finally {
    window.clearInterval(ticker);
    busy.value = false;
  }
}

/** The person confirms the proposed request: walk its own allowed actions (submit, then accept). */
async function confirm(): Promise<void> {
  let current = request.value;
  if (current === null || confirming.value) return;
  confirming.value = true;
  problem.value = "";
  try {
    for (let step = 0; step < 2; step += 1) {
      const next = ["submit", "accept"].find((a) => current?.allowed_actions.includes(a as never));
      if (next === undefined || current === null) break;
      current = await command<MeasurementRequest>(
        session,
        `/v1/measurement-requests/${encodeURIComponent(current.request_id)}/actions`,
        { action: next },
        current.revision,
      );
      request.value = current;
    }
  } catch (failure) {
    problem.value = refusal(failure);
  } finally {
    confirming.value = false;
  }
}
</script>

<template>
  <div
    class="ask"
    :class="{ active: asked !== '' }"
    data-testid="assistant-conversation"
  >
    <div
      v-if="asked"
      class="bubble person"
      data-testid="assistant-asked"
    >
      <span class="speaker">{{ LABELS.person }}</span>
      <span>{{ asked }}</span>
    </div>

    <p
      v-if="busy && !reply"
      class="wait"
      data-testid="assistant-waiting"
    >
      {{ STATES.waiting }} {{ LABELS.waiting_time }}: {{ waited }} {{ LABELS.seconds }}
    </p>

    <div
      v-if="reply && reply.role === 'notice'"
      class="bubble notice"
      data-testid="assistant-notice"
    >
      {{ reply.text }}
    </div>

    <div
      v-else-if="reply"
      class="bubble reply"
      data-testid="assistant-reply"
    >
      <p
        v-if="reply.text"
        class="said"
      >
        {{ reply.text }}
      </p>
      <template v-if="action && action.action === 'request_measurement'">
        <p class="said">
          {{ action.reason }}
        </p>
        <p class="fine">
          {{ LABELS.decision_affected }}: {{ action.decision_affected }}
        </p>
      </template>
      <p
        v-else-if="action && action.action === 'ask'"
        class="said"
      >
        {{ action.question }}
      </p>
      <ul
        v-if="reply.limitations.length > 0"
        class="limits"
        data-testid="assistant-limitations"
      >
        <li
          v-for="limit in reply.limitations"
          :key="limit"
        >
          {{ limit }}
        </li>
      </ul>
      <div
        v-if="request"
        class="request"
        data-testid="assistant-request"
      >
        <p
          v-if="kitLine"
          class="fine"
        >
          {{ kitLine }}
        </p>
        <button
          v-if="!confirmed"
          type="button"
          class="pill"
          data-testid="assistant-take-reading"
          :disabled="confirming"
          @click="confirm"
        >
          {{ ASK_WORDS.takeReading }}
        </button>
        <p
          v-else
          class="fine"
          data-testid="assistant-request-confirmed"
        >
          {{ ASK_WORDS.requested }}
          <RouterLink
            class="go"
            :to="{ name: 'pulse', query: request.episode_id ? { episode: request.episode_id } : {} }"
          >
            {{ ASK_WORDS.openPulse }}
          </RouterLink>
        </p>
      </div>
    </div>

    <p
      v-if="problem"
      class="fine"
      role="alert"
    >
      {{ problem }}
    </p>

    <form
      v-if="running"
      class="field"
      @submit.prevent="send"
    >
      <input
        v-model="draft"
        type="text"
        maxlength="500"
        :aria-label="ASK_WORDS.placeholder"
        :placeholder="ASK_WORDS.placeholder"
        :disabled="busy"
        data-testid="assistant-input"
      >
      <button
        type="submit"
        class="pill"
        :disabled="busy || draft.trim() === ''"
        data-testid="assistant-send"
      >
        {{ LABELS.send }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.ask { display: flex; flex: 1 1 auto; flex-direction: column; align-self: stretch; gap: 8px; min-height: 0; overflow-y: auto; margin-top: 8px; }
.bubble { border-radius: 14px; padding: 8px 12px; font-size: 15px; line-height: 21px; }
.person { align-self: flex-end; background: rgba(255, 255, 255, 0.16); display: flex; gap: 8px; }
.person .speaker { opacity: 0.7; }
.reply, .notice { background: rgba(0, 0, 0, 0.18); }
.said { font-size: 17px; line-height: 24px; font-weight: 300; }
.fine, .wait { font-size: 14px; line-height: 19px; opacity: 0.85; }
.limits { margin: 4px 0 0; padding-left: 18px; font-size: 14px; line-height: 19px; }
.request { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 6px; }
.field { display: flex; align-self: stretch; gap: 8px; margin-top: auto; }
.field input {
  flex: 1 1 auto;
  min-width: 0;
  border: 0;
  border-radius: 999px;
  padding: 8px 14px;
  font: inherit;
  font-size: 15px;
  color: #113324;
  background: rgba(255, 255, 255, 0.92);
}
.pill {
  border: 0;
  border-radius: 999px;
  padding: 8px 16px;
  font: inherit;
  font-size: 15px;
  font-weight: 500;
  color: #113324;
  background: #d8f5c8;
  cursor: pointer;
}
.pill:disabled { opacity: 0.55; cursor: default; }
.go { color: inherit; text-decoration: underline; margin-left: 6px; }
</style>
