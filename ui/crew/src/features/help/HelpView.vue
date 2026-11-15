<script setup lang="ts">
import type { components } from "@crew-health/contracts";
import { computed, onMounted, ref, shallowRef, watchEffect } from "vue";
import { useRouter } from "vue-router";
import { useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { copy, originWord } from "../../design/copy";
import { useStage } from "../../shell/stage";
import { CHROME } from "../../design/chrome";
import { postIfMatch } from "./hubCommand";
import {
  assistantWords,
  cachedFirstScreen,
  modelState,
  notice,
  phase,
  sessionScreen,
  type AssistantStatus,
  type HelpScreen,
  type HelpSession,
} from "./helpModel";
import { ASSISTANT_TITLE, GROUND_TITLE } from "./labels";

const HELP_NAME = CHROME.someoneNeedsHelp;

type GroundLink = components["schemas"]["ground-link.schema"];

const api = useCrewApi();
const session = useSession();
const router = useRouter();
const stage = useStage();

const openedAt = new Date().toISOString();
const help = shallowRef<HelpSession | null>(null);
const status = shallowRef<AssistantStatus | null>(null);
const link = shallowRef<GroundLink | null>(null);
const queued: { node_id: string; answer_index: number; at: string }[] = [];
const refused = ref<string | null>(null);
const busy = ref(false);

/** The cached first screen renders in the same tick as the route: nothing here waits for the hub. */
const screen = computed<HelpScreen>(() => (help.value === null ? cachedFirstScreen() : sessionScreen(help.value)));
const fallbackState = ref<string | null>(null);
const words = computed(() => assistantWords(modelState(help.value, status.value) ?? fallbackState.value));
const progress = computed(() => phase(screen.value));
const who = computed(() => session.current?.person?.display_name ?? session.current?.actor.display_name ?? "");
const linkLine = computed(() => {
  const current = link.value;
  if (current === null) {
    return "";
  }
  const word = copy(`ground_link_words.${current.status}`);
  return current.source === "demo_switch" ? `${word}, ${originWord("SIM")}` : word;
});
const logLines = computed(() =>
  (help.value?.log ?? []).map((entry) => {
    const at = new Date(entry.at);
    return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}  ${entry.text}`;
  }),
);

watchEffect(() => {
  stage.subline = who.value;
  stage.headline = screen.value.kind === "flow" ? screen.value.question : HELP_NAME;
  stage.focus = null;
  stage.marks = [];
});

function problem(failure: unknown): string {
  const code = typeof failure === "object" && failure !== null && "code" in failure ? String(failure.code) : "INTERNAL_ERROR";
  const message = copy(`error_messages.${code}`);
  return message.startsWith("error_messages.") ? code : message;
}

async function act(body: Record<string, unknown>): Promise<void> {
  const current = help.value;
  if (current === null) {
    return;
  }
  busy.value = true;
  try {
    help.value = await postIfMatch<HelpSession>(
      session,
      `/v1/help-sessions/${encodeURIComponent(current.help_session_id)}/actions`,
      { ...body, client_time: new Date().toISOString() },
      current.revision,
    );
    refused.value = null;
  } catch (failure) {
    refused.value = problem(failure);
  } finally {
    busy.value = false;
  }
}

function answer(index: number): void {
  if (help.value === null) {
    /* the hub has not answered yet: the answer is kept and replayed in order when the session opens */
    queued.push({ node_id: screen.value.nodeId, answer_index: index, at: new Date().toISOString() });
    return;
  }
  void act({ action: "answer", node_id: screen.value.nodeId, answer_index: index });
}

function tick(index: number, done: boolean): void {
  void act({ action: done ? "untick_step" : "tick_step", step_index: index });
}

async function open(): Promise<void> {
  try {
    const opened = await api.post<HelpSession>("/v1/help-sessions", {
      subject: { kind: "unknown" },
      entry: { kind: "button" },
      client_opened_at: openedAt,
      offline_path: queued.splice(0),
    });
    help.value = opened;
  } catch (failure) {
    refused.value = problem(failure);
  }
}

onMounted(() => {
  void open();
  /* both at once: the assistant route when the hub has one, else the health document's model state */
  void api
    .get<AssistantStatus>("/v1/assistant/status")
    .then((value) => (status.value = value))
    .catch(() => (status.value = null));
  void api
    .getHealth()
    .then((health) => (fallbackState.value = health.assistant_state))
    .catch(() => (fallbackState.value = null));
  void api
    .get<GroundLink>("/v1/ground-link")
    .then((value) => (link.value = value))
    .catch(() => (link.value = null));
});
</script>

<template>
  <div
    class="urgent"
    data-test="help"
  >
    <article
      class="card paper hp-main"
      role="dialog"
      aria-modal="false"
      aria-labelledby="urgent-title"
      data-test="help-main"
    >
      <div class="card-head">
        <h2
          id="urgent-title"
          class="card-title"
        >
          {{ HELP_NAME }}
        </h2>
        <button
          class="go"
          type="button"
          :aria-label="HELP_NAME"
          @click="router.back()"
        >
          <svg
            class="icon"
            viewBox="0 0 24 24"
          ><path d="M18 6 6 18M6 6l12 12" /></svg>
        </button>
      </div>
      <div
        class="swatches"
        data-test="help-swatches"
      >
        <span
          v-if="words.model"
          data-test="help-model"
        >{{ words.model }}</span>
        <span data-test="help-works">{{ words.works }}</span>
      </div>
      <div class="steps">
        <i
          v-for="k in 3"
          :key="k"
          :class="{ done: k - 1 < progress, now: k - 1 === progress }"
        />
      </div>
      <div class="flow-body">
        <p
          class="ask-q"
          data-test="help-question"
        >
          {{ screen.question }}
        </p>
        <p
          v-if="screen.hint"
          class="sub"
        >
          {{ screen.hint }}
        </p>
        <div
          v-if="screen.answers.length"
          class="pills"
        >
          <button
            v-for="a in screen.answers"
            :key="a.index"
            class="pill"
            type="button"
            :disabled="busy"
            data-test="help-answer"
            @click="answer(a.index)"
          >
            {{ a.label }}
          </button>
        </div>
        <div
          v-if="screen.steps.length"
          class="nsteps"
        >
          <button
            v-for="s in screen.steps"
            :key="s.index"
            class="li tap"
            :class="{ done: s.done }"
            type="button"
            @click="tick(s.index, s.done)"
          >
            <span class="what">{{ s.text }}</span>
          </button>
        </div>
        <template v-if="screen.callGroundWhen">
          <p class="group-k">
            {{ copy("plan_stage_words.ground_log") }}
          </p>
          <p class="sub">
            {{ screen.callGroundWhen }}
          </p>
        </template>
        <p
          v-if="refused"
          class="route"
          data-test="help-refused"
        >
          {{ copy("designed_states.not_available_here") }}
        </p>
        <p
          v-if="refused"
          class="fine"
          data-test="help-refused-code"
        >
          {{ refused }}
        </p>
        <p class="fine help-notice">
          {{ notice }}
        </p>
      </div>
    </article>

    <article
      class="card g g-deep hp-asst"
      data-test="help-assistant"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ ASSISTANT_TITLE }}
        </h2>
      </div>
      <div
        v-if="words.model"
        class="swatches"
      >
        <span><i class="off" />{{ words.model }}</span>
      </div>
      <p
        class="say"
        data-test="help-assistant-line"
      >
        {{ words.line }}
      </p>
    </article>

    <article class="card paper hp-log">
      <div class="card-head">
        <h2 class="card-title">
          {{ GROUND_TITLE }}
        </h2>
        <span
          class="src"
          data-test="help-link"
        >{{ linkLine }}</span>
      </div>
      <div
        class="log"
        data-test="help-log"
      >
        <div
          v-for="(line, k) in logLines"
          :key="k"
        >
          {{ line }}
        </div>
      </div>
    </article>
  </div>
</template>

<style scoped>
.hp-asst .swatches i.off { background: rgba(255, 255, 255, 0.4); }
.hp-asst .say { font-size: 28px; line-height: 34px; }
.nsteps button.li { width: 100%; text-align: left; background: none; border-left: 0; border-right: 0; border-bottom: 0; cursor: pointer; }
.help-notice { margin-top: auto; padding-top: 14px; }
.src { font-size: 12px; line-height: 16px; color: var(--ink-3); white-space: nowrap; }
</style>
